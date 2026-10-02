"""后台低频召回；调度游标独立持久化，风控时只使用本地池。"""
import threading
import time
import json
from collections import OrderedDict

from backend.config import FOLLOWINGS_INTERVAL, ARCHIVE_UPS_PER_HOUR, RELATED_SEEDS_PER_REFRESH, SOURCE_DETAIL_BUFFER
from backend.services.affinity import affinity, up_key
from backend.services.bilibili_service import bili, BiliError, LoginExpired
from backend.services.cache_service import pool
from backend.services.filter_service import filters
from backend.services.source_mixer import settings, quotas
from backend.storage.database import connect, get_state, set_state
from backend.services import feed_performance as perf
from backend.services import request_coordination as requests_scope


class SourceScheduler:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._wake = threading.Event()
        self._demands = OrderedDict()
        self.last_error = None

    def start(self):
        with perf.lock(self._start_lock, "scheduler_start_lock"):
            if self._thread and self._thread.is_alive():
                if self._stop.is_set():
                    worker = self._thread
                    def resume():
                        worker.join()
                        if bili.has_cookie:
                            self.start()
                    threading.Thread(target=resume, name="sources-resume", daemon=True).start()
                return
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self.run, args=(self._stop,), name="source-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        bili.wake_requests()
        with self._start_lock:
            self._demands.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=12)
        with connect() as conn:
            conn.execute("DELETE FROM followings")
            conn.execute("DELETE FROM app_state WHERE key LIKE 'sources:cursor:%' OR key='sources:followings'")
            conn.execute("DELETE FROM candidates WHERE source IN ('follow','up_archive','related')")
        for source in ("follow", "up_archive", "related"):
            set_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})

    def run(self, stop):
        while not stop.is_set() and bili.has_cookie:
            if not bili.rate_limited:
                try:
                    self.tick(stop=stop)
                except LoginExpired:
                    break
                except BiliError as error:
                    self.last_error = {"time": time.time(), "code": error.code}
                except requests_scope.BudgetEnded:
                    pass
                except Exception as error:
                    self.last_error = {"time": time.time(), "code": type(error).__name__}
            finished = time.perf_counter()
            self._wake.wait(max(10, bili.rate_limited_until - time.time()))
            self._wake.clear()
            # 唤醒不绕过失败/预算耗尽后的最小重试节奏。
            stop.wait(max(0, 10 - (time.perf_counter() - finished)))

    def request_refill(self, feed_type, category, limit, options, model_version):
        """前台只登记有限需求，不同步执行储备检查或接口请求。"""
        key = (feed_type, category, limit, json.dumps(options, sort_keys=True), model_version)
        with self._start_lock:
            for expired in [k for k, v in self._demands.items() if v["seen_at"] < time.perf_counter() - 600]:
                del self._demands[expired]
            self._demands.setdefault(key, {}).update(seen_at=time.perf_counter(), feed_type=feed_type,
                                                    category=category, limit=limit, options=dict(options), model_version=model_version)
            self._demands.move_to_end(key)
            while len(self._demands) > 8:
                self._demands.popitem(last=False)
        self._wake.set()
        self.start()

    def sync_followings(self, stop=None):
        state = get_state("sources:followings", {"page": 1, "items": [], "synced_at": 0})
        if state["synced_at"] > time.time() - FOLLOWINGS_INTERVAL:
            return
        while not stop or not stop.is_set():
            page = bili.followings(state["page"])
            if stop and stop.is_set():
                return
            state["items"] += page["items"]
            if page["has_more"]:
                state["page"] += 1
                set_state("sources:followings", state)
            else:
                with connect() as conn:
                    conn.execute("DELETE FROM followings")
                    conn.executemany("INSERT OR REPLACE INTO followings(mid,name,synced_at) VALUES(?,?,?)", [(v["mid"], v["name"], time.time()) for v in state["items"]])
                set_state("sources:followings", {"page": 1, "items": [], "synced_at": time.time()})
                break

    @perf.background("source_scheduler")
    @requests_scope.background
    def tick(self, stop=None):
        if not perf.try_lock(self._lock, "source_scheduler_lock_busy_count"):
            return
        try:
            self.sync_followings(stop)
            if not settings()["classic"]:
                self._refill(stop)
                return
            snapshot = affinity.snapshot()
            from backend.services.recommendation_service import recommendation
            cooled = recommendation._cooled_bvids()
            for source in ("follow", "up_archive", "related", "rcmd", "hot"):
                if bili.rate_limited or (stop and stop.is_set()):
                    return
                if source in ("hot", "rcmd") and settings()[source] == "off" and not settings()["classic"]:
                    continue
                if pool.is_stale(source):
                    pool.expand(source, stop=stop)
                candidates, _ = filters.filter_candidates(pool.all((source,)))
                candidates = [v for v in candidates if v["bvid"] not in snapshot["watched"] and (v["bvid"], source) not in cooled]
                complete = sum(v.get("_detail_complete", False) for v in affinity.quality_gate([v for v in candidates if v.get("_detail_complete")], snapshot))
                if complete < SOURCE_DETAIL_BUFFER:
                    pool.complete((source,), SOURCE_DETAIL_BUFFER - complete, stop=stop, exclude=snapshot["watched"] | {bvid for bvid, origin in cooled if origin == source})
        finally:
            self._lock.release()

    def _refill(self, stop=None):
        from backend.services.recommendation_service import recommendation
        with self._start_lock:
            for key in [k for k, v in self._demands.items() if v["seen_at"] < time.perf_counter() - 600]:
                del self._demands[key]
            demands = list(self._demands.items())
        with requests_scope.scope(background=True, total=15, details=12, recalls=3, stop=stop, candidate=True):
            attempts = 0
            for key, demand in demands:
                with self._start_lock:
                    if key in self._demands:
                        self._demands.move_to_end(key)
                pages, counts = recommendation.ready_buffer(demand["feed_type"], demand["category"], demand["limit"], demand["options"], demand["model_version"])
                if pages >= (3 if demand.get("refilling") else 2):
                    demand["refilling"] = False
                    for source in recommendation._mixed_sources(demand["feed_type"], demand["options"]):
                        if pool.is_stale(source) and not (source in ("hot", "rcmd") and settings()[source] == "off"):
                            pool.expand(source, category=demand["category"], details=False, stop=stop, seed_budget=1, head_only=True)
                    continue
                demand["refilling"] = True
                targets = quotas(demand["limit"], demand["options"]) if demand["feed_type"] == "for_you" else {s: demand["limit"] for s in recommendation._mixed_sources(demand["feed_type"], demand["options"])}
                sources = sorted(recommendation._mixed_sources(demand["feed_type"], demand["options"]), key=lambda s: -(targets.get(s, 0)*3 - counts.get(s, 0)))
                snapshot = affinity.snapshot()
                tried = demand.setdefault("tried", {})
                for expired in [k for k, timestamp in tried.items() if timestamp < time.perf_counter()-60]:
                    del tried[expired]
                for source in sources:
                    if bili.rate_limited or stop and stop.is_set():
                        return
                    if source in ("hot", "rcmd") and settings()[source] == "off":
                        continue
                    candidates = pool.all((source,))
                    expanded = False
                    if pool.is_stale(source) or not candidates:
                        pool.expand(source, category=demand["category"], details=False, stop=stop, seed_budget=1, head_only=bool(candidates))
                        expanded = True
                    while True:
                        pages, _ = recommendation.ready_buffer(demand["feed_type"], demand["category"], demand["limit"], demand["options"], demand["model_version"])
                        if pages >= 3:
                            demand["refilling"] = False
                            break
                        cooled = recommendation._cooled_bvids() if demand["feed_type"] != "following" else set()
                        pending, _ = filters.filter_candidates([v for v in pool.all((source,)) if v.get("_detail_complete") is not True and tried.get((v["bvid"], source), 0) < time.perf_counter()-60], record=False)
                        if demand["feed_type"] != "following":
                            hidden = recommendation._hidden_bvids()
                            pending = [v for v in pending if v["bvid"] not in snapshot["watched"] | hidden and (v["bvid"], source) not in cooled and
                                       (source != "follow" or up_key(v) in snapshot["followings"] and (v.get("pubdate") or 0) >= time.time()-30*86400) and
                                       (source != "up_archive" or snapshot["ups"].get(up_key(v), {}).get("regular"))]
                        else:
                            pending.sort(key=lambda v: v.get("pubdate") or 0, reverse=True)
                        if not pending:
                            if not expanded:
                                pool.expand(source, category=demand["category"], details=False, stop=stop, seed_budget=1)
                                expanded = True
                                continue
                            break
                        if pending[0]["bvid"] not in bili._detail_cache:
                            if attempts >= 12:
                                return
                            attempts += 1
                        tried[(pending[0]["bvid"], source)] = time.perf_counter()
                        pool.complete((source,), 1, stop=stop, candidates=pending[:1])
                    if pages >= 3:
                        break

    def fetch(self, source, state, category="all", stop=None, seed_budget=None, head_only=False):
        if source == "follow":
            cursor = get_state("sources:cursor:follow", {})
            # 每轮先取最新动态；缓冲不足时继续回溯旧页，关注页不限发布时间。
            page = bili.follow_feed()
            videos = page["items"]
            if not cursor:
                cursor = {"offset": page["offset"], "has_more": page["has_more"]}
            elif cursor.get("has_more") and not head_only:
                try:
                    older = bili.follow_feed(cursor["offset"])
                    videos += older["items"]
                    cursor = {"offset": older["offset"], "has_more": older["has_more"]}
                except LoginExpired:
                    raise
                except BiliError as error:
                    self.last_error = {"time": time.time(), "code": error.code}
            if not stop or not stop.is_set():
                set_state("sources:cursor:follow", cursor)
            return videos
        if source == "related":
            return self.expand_related(category, stop, seed_budget)
        cursor = get_state("sources:cursor:up_archive", {})
        if cursor.get("hour", 0) <= time.time() - 3600:
            cursor["hour"], cursor["used"] = time.time(), []
        snapshot = affinity.snapshot()
        ups = [dict(mid=up["mid"], author=up["name"]) for up in snapshot["ups"].values() if up["regular"] and up["mid"]]
        ups, _ = filters.filter_candidates(ups)
        ups = sorted((up for up in ups if not up.get("expand_disabled") and not up.get("downrank") and up_key(up) not in cursor["used"] and not cursor.get(up_key(up), {}).get("done")), key=lambda v: up_key(v) not in snapshot["followings"])
        videos = []
        for up in ups[:max(0, ARCHIVE_UPS_PER_HOUR - len(cursor["used"]))]:
            key = up_key(up)
            archive = cursor.get(key, {"page": 1, "order": "click", "done": False})
            if archive["done"]:
                continue
            page = bili.up_archives(up["mid"], archive["page"], archive["order"])
            for video in page["items"]:
                video["archive_order"] = archive["order"]
            videos += page["items"]
            if archive["order"] == "click":
                archive = {"page": 1, "order": "pubdate", "done": False}
            else:
                archive["page"] += 1
                archive["done"] = not page["has_more"]
            cursor[key] = archive
            cursor["used"].append(key)
            if stop and stop.is_set():
                return []
            pool._save("up_archive", page["items"])
            set_state("sources:cursor:up_archive", cursor)
        return videos

    def expand_related(self, category="all", stop=None, seed_budget=None):
        cursor = get_state("sources:cursor:related", {"used": []})
        videos = []
        for seed in affinity.seeds(category, cursor["used"], min(RELATED_SEEDS_PER_REFRESH, seed_budget) if seed_budget is not None else RELATED_SEEDS_PER_REFRESH):
            items = bili.related(seed["bvid"])
            for video in items:
                video["seed_title"] = seed.get("title", "")
            videos += items
            if stop and stop.is_set():
                return []
            pool._save("related", items)
            cursor["used"].append(seed["bvid"])
            set_state("sources:cursor:related", cursor)
        return videos


scheduler = SourceScheduler()
