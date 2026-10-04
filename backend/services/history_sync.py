"""后台增量收集，逐条保存，失败不会丢失本页已成功记录。"""
import json
import time

from backend.config import HISTORY_PATH
from backend.services.bilibili_service import bili, RateLimited, BiliError, LoginExpired
from backend.services.training_data import record_history
from backend.storage.database import connect, get_state, set_state
from backend.services import feed_performance as perf
from backend.services import request_coordination as requests_scope


class HistorySync:
    def bootstrap(self):
        if get_state("v03_history_imported", False):
            return
        try:
            with open(HISTORY_PATH, encoding="utf-8") as file:
                for video in json.load(file):
                    record_history(video)
        except FileNotFoundError:
            pass
        set_state("v03_history_imported", True)

    def enrich(self, video, cached=None):
        if bili.rate_limited:
            raise RateLimited(-352, "rate limited")
        # 收集器自身限速，不延迟正常 API 请求。
        time.sleep(1)
        try:
            detail = cached or bili.detail(video["bvid"])
        except (RateLimited, LoginExpired):
            raise
        except BiliError as error:
            if error.code not in (-404, -400, -403, 62002, 62012):
                raise
            # 已删除/失效视频保存原始行为与缺失字段，不阻塞整页断点。
            return {**video, "collection_error": str(error)}
        enriched = {**detail, **video}
        enriched["tag"] = detail.get("tag", [])
        if video.get("source") == "history":
            time.sleep(1)
            try:
                enriched["isliked"] = bili.get("https://api.bilibili.com/x/web-interface/archive/has/like",
                                             {"aid": detail.get("aid")}) if detail.get("aid") else None
            except (RateLimited, LoginExpired):
                raise
            except BiliError:
                enriched["isliked"] = None
        return enriched

    @perf.background("history_sync")
    @requests_scope.collection
    def run(self, force=False, page_budget=10, stop=None):
        self.bootstrap()
        state = get_state("v03_history_sync", {})
        if not force and state.get("completed_at", 0) > time.time() - 86400:
            return False
        if state.get("complete"):
            with connect() as conn:
                known = [r[0] for r in conn.execute("SELECT DISTINCT bvid FROM history_events")]
            state = {"incremental": True, "known": known}
        state.setdefault("cursor", {"max": 0, "view_at": 0})
        state.setdefault("history_bvids", [])
        state.setdefault("favorite_bvids", [])
        state.setdefault("folder_index", 0)
        state.setdefault("favorite_page", 1)
        changed = False
        for _ in range(page_budget):
            if stop and stop.is_set():
                return changed
            if bili.rate_limited:
                break
            if not state.get("history_done"):
                time.sleep(1)
                page = bili.watch_history(**{"max_cursor": state["cursor"]["max"],
                                            "view_at": state["cursor"]["view_at"], "ps": 20})
                for video in page["items"]:
                    if stop and stop.is_set():
                        return changed
                    if video["bvid"] not in state["history_bvids"]:
                        with connect() as conn:
                            previous = conn.execute("SELECT video FROM history_events WHERE bvid=? ORDER BY observed_at DESC,id DESC LIMIT 1", (video["bvid"],)).fetchone()
                        # 日常增量复用完整特征，仍同步最新进度，避免重新请求千条详情。
                        record_history(self.enrich(video, json.loads(previous[0]) if previous and state.get("incremental") else None))
                        state["history_bvids"].append(video["bvid"])
                        set_state("v03_history_sync", state)
                        changed = True
                    if len(state["history_bvids"]) >= 1000:
                        break
                if (len(state["history_bvids"]) >= 1000 or not page["has_more"] or page["cursor"] == state["cursor"]
                        or state.get("incremental") and page["items"] and all(v["bvid"] in state["known"] for v in page["items"])):
                    state["history_done"] = True
                state["cursor"] = page["cursor"]
            else:
                if "folders" not in state:
                    state["folders"] = [f["id"] for f in bili.fav_folders()]
                if state["folder_index"] >= len(state["folders"]) or len(state["favorite_bvids"]) >= 200:
                    state.update(complete=True, completed_at=time.time())
                    set_state("v03_history_sync", state)
                    return changed
                time.sleep(1)
                page = bili.fav_videos(state["folders"][state["folder_index"]], state["favorite_page"])
                for video in page["items"]:
                    if stop and stop.is_set():
                        return changed
                    if video["bvid"] not in state["favorite_bvids"]:
                        with connect() as conn:
                            previous = conn.execute("SELECT video FROM history_events WHERE bvid=? ORDER BY observed_at DESC,id DESC LIMIT 1", (video["bvid"],)).fetchone()
                        record_history(self.enrich(video, json.loads(previous[0]) if previous and state.get("incremental") else None))
                        state["favorite_bvids"].append(video["bvid"])
                        set_state("v03_history_sync", state)
                        changed = True
                    if len(state["favorite_bvids"]) >= 200:
                        break
                if page["has_more"] and not (state.get("incremental") and page["items"] and all(v["bvid"] in state["known"] for v in page["items"])):
                    state["favorite_page"] += 1
                else:
                    state["folder_index"] += 1
                    state["favorite_page"] = 1
            set_state("v03_history_sync", state)
        return changed


sync = HistorySync()
