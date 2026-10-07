"""后台低频召回；调度游标独立持久化，风控时只使用本地池。"""
import threading
import time
import hashlib
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
        self.enabled = True
        self._session_stopped = False
        self._worker_stop = self._stop
        self._idle_check = 0

    def resume_session(self):
        self._session_stopped = False
        self._stop = threading.Event()

    def start(self):
        if not self.enabled or self._session_stopped or not bili.has_cookie:
            return
        with perf.lock(self._start_lock, "scheduler_start_lock"):
            if not self.enabled or self._session_stopped or not bili.has_cookie:
                return
            if self._thread and self._thread.is_alive():
                if self._worker_stop.is_set():
                    worker = self._thread
                    def resume():
                        worker.join()
                        if self.enabled and not self._session_stopped and bili.has_cookie:
                            self.start()
                    threading.Thread(target=resume, name="sources-resume", daemon=True).start()
                return
            self._stop = threading.Event()
            self._worker_stop = self._stop
            self._thread = threading.Thread(target=self.run, args=(self._stop,), name="source-scheduler", daemon=True)
            self._thread.start()

    def stop(self):
        self._session_stopped = True
        self._worker_stop.set()
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
            conn.execute("DELETE FROM candidate_sources WHERE source IN ('follow','up_archive','related','vertical_search')")
            conn.execute("DELETE FROM candidates WHERE NOT EXISTS(SELECT 1 FROM candidate_sources s WHERE s.bvid=candidates.bvid)")
        for source in ("follow", "up_archive", "related"):
            set_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})

    def run(self, stop):
        while not stop.is_set() and bili.has_cookie:
            if not bili.rate_limited:
                try:
                    self.tick(stop=stop)
                except LoginExpired:
                    from backend.services.policy_review import pause
                    pause("登录已失效，采集暂停；重新登录后继续本轮")
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
        if not self.enabled or self._session_stopped or not bili.has_cookie:
            return
        if feed_type == "for_you" and category != "all":
            recent = get_state("sources:warm_categories", {})
            recent = {k: v for k, v in recent.items() if v > time.time()-86400}
            recent[category] = time.time()
            set_state("sources:warm_categories", dict(sorted(recent.items(), key=lambda item: item[1])[-2:]))
        key = (feed_type, category, limit, json.dumps(options, sort_keys=True), model_version)
        with self._start_lock:
            if self._session_stopped:
                return
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
                special = None
                if any(v.get("special") is None for v in state["items"]):
                    try:
                        special = bili.special_followings()
                        set_state("sources:special_status", {"status": "success", "at": time.time()})
                    except LoginExpired:
                        raise
                    except BiliError as error:
                        set_state("sources:special_status", {"status": "failed", "at": time.time(), "code": error.code})
                if stop and stop.is_set():
                    return
                if all(v.get("special") is not None for v in state["items"]):
                    set_state("sources:special_status", {"status": "success", "at": time.time(), "method": "followings"})
                with connect() as conn:
                    previous = {str(r[0]): r[1] for r in conn.execute("SELECT mid,special FROM followings")}
                    conn.execute("DELETE FROM followings")
                    conn.executemany("INSERT OR REPLACE INTO followings(mid,name,synced_at,special) VALUES(?,?,?,?)", [(v["mid"], v["name"], time.time(), int(str(v["mid"]) in special) if special is not None else v.get("special") if v.get("special") is not None else previous.get(str(v["mid"]))) for v in state["items"]])
                set_state("sources:followings", {"page": 1, "items": [], "synced_at": time.time()})
                break

    @perf.background("source_scheduler")
    @requests_scope.background
    def tick(self, stop=None):
        if not perf.try_lock(self._lock, "source_scheduler_lock_busy_count"):
            return
        try:
            if not self.enabled or self._session_stopped or stop and stop.is_set():
                return
            from backend.services import policy_review
            if not policy_review.pending() and not settings()["classic"] and not any(k[0] != "idle" and v["seen_at"] >= time.perf_counter()-600 for k, v in self._demands.items()) and self._idle_check > time.time()-300:
                return
            self._idle_check = time.time()
            with requests_scope.scope(total=15, details=12, recalls=3, stop=stop, candidate=True):
                from backend.services.interest_profile import publish, snapshot
                if (snapshot().get("published_at") or 0) < time.time()-300:
                    publish()
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
                    if source in ("hot", "rcmd", "vertical_search") and settings()[source] == "off":
                        continue
                    if pool.is_stale(source):
                        pool.expand(source, stop=stop)
                    candidates, _ = filters.filter_candidates(pool.all((source,)))
                    candidates = [v for v in candidates if v["bvid"] not in snapshot["watched"] and (v["bvid"], source) not in cooled]
                    complete = sum(v.get("_detail_complete", False) for v in affinity.quality_gate([v for v in candidates if v.get("_detail_complete")], snapshot))
                    if complete < SOURCE_DETAIL_BUFFER:
                        pool.complete((source,), SOURCE_DETAIL_BUFFER - complete, stop=stop, exclude=snapshot["watched"] | {bvid for bvid, origin in cooled if origin == source})
                if policy_review.pending():
                    self._blind_refill(stop)
        finally:
            try:
                if self.enabled and not self._session_stopped and not (stop and stop.is_set()):
                    self._publish_readiness()
                    from backend.services.policy_review import background
                    background()
            finally:
                self._lock.release()

    def _readiness_input(self, feed_type, category, limit):
        from backend.services.recommendation_policy import freeze
        from backend.services.model_registry import registry
        policy = freeze()
        options = {**settings(), "_policy": policy}
        version = registry.state().get("active") or "fallback-v03"
        with connect() as conn:
            updated = tuple(conn.execute("SELECT COUNT(*),COALESCE(MAX(updated_at),0) FROM candidates").fetchone())
            relations = tuple(conn.execute("SELECT COUNT(*),COALESCE(MAX(last_refresh_at),0) FROM candidate_sources").fetchone())
            feedback = tuple(conn.execute("SELECT COUNT(*),COALESCE(MAX(created_at),0),COALESCE(MAX(revoked_at),0) FROM feedback").fetchone())
            history = tuple(conn.execute("SELECT COUNT(*),COALESCE(MAX(observed_at),0) FROM history_events").fetchone())
        signature = hashlib.sha256(json.dumps([feed_type, category, limit, version, settings(), policy["signature"], policy["controls"], policy["profile"]["version"], updated, relations, feedback, history, get_state("filters:version", 0), get_state("feed:revision", 0)], sort_keys=True).encode()).hexdigest()[:20]
        return signature, options, version

    def _publish_readiness(self):
        from backend.services.recommendation_service import recommendation
        with self._start_lock:
            demands = [dict(value) for key, value in self._demands.items() if value.get("readiness") and value["seen_at"] >= time.perf_counter()-600 and value["feed_type"] in ("for_you", "explore")]
        for demand in demands:
            key = f'feed:readiness:{demand["feed_type"]}:{demand["category"]}:{demand["limit"]}'
            revision = int(get_state("feed:revision", 0))
            signature, options, version = self._readiness_input(demand["feed_type"], demand["category"], demand["limit"])
            previous = get_state(key, {})
            if previous.get("input") == signature and previous.get("status") != "checking" and previous.get("expires_at", 0) > time.time():
                continue
            pages, counts = recommendation.ready_buffer(demand["feed_type"], demand["category"], demand["limit"], options, version)
            signature_after, _, _ = self._readiness_input(demand["feed_type"], demand["category"], demand["limit"])
            if signature != signature_after:
                continue
            available = demand["limit"] if pages else sum(counts.values())
            ready_version = hashlib.sha256(json.dumps([signature, pages, available]).encode()).hexdigest()[:20]
            with connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                current = conn.execute("SELECT value FROM app_state WHERE key='feed:revision'").fetchone()
                if int(current[0] if current else 0) != revision:
                    continue
                expiry = conn.execute("SELECT MIN(at) FROM (SELECT visible_at+1800 at FROM recommendation_exposures WHERE visible_at>? UNION ALL SELECT served_at+1800 FROM recommendation_history WHERE served_at>?)", (time.time()-1800, time.time()-1800)).fetchone()[0]
                conn.execute("INSERT INTO app_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (key, json.dumps({"status": "ready" if available else "waiting", "version": ready_version, "input": signature, "complete_pages": pages, "available": available, "at_least": pages >= 3, "expires_at": min(time.time()+30, expiry or time.time()+30), "retry_at": max(demand.get("retry_at", 0), bili.rate_limited_until)}), time.time()))

    def readiness(self, stream_id):
        with connect() as conn:
            stream = conn.execute("SELECT * FROM feed_streams WHERE stream_id=?", (stream_id,)).fetchone()
            last_page = conn.execute("SELECT items FROM feed_cache WHERE stream_id=? ORDER BY page DESC LIMIT 1", (stream_id,)).fetchone()
        if not stream or stream["feed_type"] not in ("for_you", "explore"):
            raise ValueError("推荐批次不存在或不支持补池状态")
        key = f'feed:readiness:{stream["feed_type"]}:{stream["category"]}:{stream["page_limit"]}'
        with self._start_lock:
            touched = False
            for demand in self._demands.values():
                if (demand["feed_type"], demand["category"], demand["limit"]) == (stream["feed_type"], stream["category"], stream["page_limit"]):
                    demand["empty"] = not last_page or not json.loads(last_page[0])
                    demand["seen_at"] = time.perf_counter()
                    touched = True
        if not touched:
            self.request_refill(stream["feed_type"], stream["category"], stream["page_limit"], settings(), stream["model_version"])
        with self._start_lock:
            for demand in self._demands.values():
                if (demand["feed_type"], demand["category"], demand["limit"]) == (stream["feed_type"], stream["category"], stream["page_limit"]):
                    demand["readiness"] = True
        value = get_state(key, {})
        signature, _, _ = self._readiness_input(stream["feed_type"], stream["category"], stream["page_limit"])
        if value.get("input") != signature or value.get("expires_at", 0) <= time.time():
            self._wake.set()
            return {"status": "checking", "version": None, "complete_pages": 0, "available": 0, "retry_at": bili.rate_limited_until}
        return value

    def _refill(self, stop=None):
        from backend.services.recommendation_service import recommendation
        from backend.services import vertical_search, interest_profile
        with self._start_lock:
            for key in [k for k, v in self._demands.items() if v["seen_at"] < time.perf_counter() - 600]:
                del self._demands[key]
            from backend.services.model_registry import registry
            for category in ["all"] + [k for k, v in get_state("sources:warm_categories", {}).items() if v > time.time()-86400]:
                key = ("idle", category)
                self._demands.setdefault(key, {"seen_at": float("inf"), "feed_type": "for_you", "category": category, "limit": 12})
                self._demands[key].update(options=settings(), model_version=registry.state().get("active") or "fallback-v03")
            for key in [k for k in self._demands if k[0] == "idle" and k[1] != "all" and get_state("sources:warm_categories", {}).get(k[1], 0) <= time.time()-86400]:
                del self._demands[key]
            demands = sorted(self._demands.items(), key=lambda item: (not item[1].get("empty", False), item[0][0] == "idle"))
        from backend.services.policy_review import pending
        if pending():
            demands.append((("blind", "all"), {"blind": True}))
        with requests_scope.scope(background=True, total=None if requests_scope.current().get("budget") else 15, details=12, recalls=3, stop=stop, candidate=True):
            attempts = 0
            for key, demand in demands:
                if demand.get("blind"):
                    self._blind_refill(stop)
                    continue
                if demand.get("retry_at", 0) > time.time():
                    continue
                before = sum(bool(v.get("_detail_complete")) for v in pool.all(tuple(pool._locks)))
                with self._start_lock:
                    if key in self._demands:
                        self._demands.move_to_end(key)
                pages, counts = recommendation.ready_buffer(demand["feed_type"], demand["category"], demand["limit"], demand["options"], demand["model_version"])
                if pages >= (3 if demand.get("refilling") else 2):
                    demand["refilling"] = False
                    for source in recommendation._mixed_sources(demand["feed_type"], demand["options"]):
                        if source != "vertical_search" and pool.is_stale(source) and not (source in ("hot", "rcmd") and settings()[source] == "off"):
                            pool.expand(source, category=demand["category"], details=False, stop=stop, seed_budget=1, head_only=True)
                    continue
                demand["refilling"] = True
                # 提前登记无成果退避，预算耗尽或异常退出也不能连续重试。
                demand["retry_at"] = time.time()+900
                targets = quotas(demand["limit"], demand["options"]) if demand["feed_type"] == "for_you" else {s: demand["limit"] for s in recommendation._mixed_sources(demand["feed_type"], demand["options"])}
                sources = sorted(recommendation._mixed_sources(demand["feed_type"], demand["options"]), key=lambda s: -(targets.get(s, 0)*3 - counts.get(s, 0)))
                snapshot = affinity.snapshot()
                from backend.services.interest_profile import special_ups, replay_eligible
                special, replay = special_ups(), replay_eligible(snapshot)
                tried = demand.setdefault("tried", {})
                for expired in [k for k, timestamp in tried.items() if timestamp < time.perf_counter()-60]:
                    del tried[expired]
                for source in sources:
                    if bili.rate_limited or stop and stop.is_set():
                        return
                    if source in ("hot", "rcmd", "vertical_search") and settings()[source] == "off" and (source != "vertical_search" or not vertical_search.directions(interest_profile.snapshot())):
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
                        if source == "vertical_search":
                            from backend.services.vertical_search import eligible_reasons
                            from backend.services.interest_profile import settings as interest_settings
                            config = interest_settings()
                            from backend.services.recommendation_policy import freeze
                            controls = freeze(False)["controls"]
                            pending = [v for v in pending if eligible_reasons(v.get("source_reasons", {}).get(source, []), config, special, controls, v, settings()["vertical_search"] != "off" and demand["feed_type"] != "explore")]
                        if demand["category"] not in ("all", "recent", "discover"):
                            # 列表召回可能没有分区；允许有限补全，完整候选仍按实际分区筛选。
                            pending = [v for v in pending if not v.get("tname") or v["tname"] == demand["category"]]
                        if demand["feed_type"] != "following":
                            hidden = recommendation._hidden_bvids()
                            pending = [v for v in pending if (v["bvid"] not in snapshot["watched"] | hidden or v["bvid"] in replay and v["bvid"] not in recommendation._hard_hidden_bvids()) and (v["bvid"], source) not in cooled and
                                       (source != "follow" or up_key(v) in snapshot["followings"] and (v.get("pubdate") or 0) >= time.time()-30*86400) and
                                       (source != "up_archive" or snapshot["ups"].get(up_key(v), {}).get("regular") or up_key(v) in special)]
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
                                if sum(bool(v.get("_detail_complete")) for v in pool.all(tuple(pool._locks))) > before:
                                    demand.pop("retry_at", None)
                                return
                            attempts += 1
                        tried[(pending[0]["bvid"], source)] = time.perf_counter()
                        pool.complete((source,), 1, stop=stop, candidates=pending[:1])
                    if pages >= 3:
                        break
                if pages >= 3 or sum(bool(v.get("_detail_complete")) for v in pool.all(tuple(pool._locks))) > before:
                    demand.pop("retry_at", None)

    def _blind_refill(self, stop):
        """普通补池之后用剩余预算低频准备，不额外开启请求预算。"""
        from backend.services.policy_review import owner
        account = owner()
        with connect() as conn:
            row = conn.execute("SELECT snapshot,preparation FROM policy_reviews WHERE owner=? AND status='preparing'", (account,)).fetchone()
        if not row:
            return
        preparation = json.loads(row["preparation"])
        if preparation.get("next_check_at", 0) > time.time():
            return
        frozen = json.loads(row["snapshot"])
        allowed = frozen["policy"]["sources"]
        # 每轮轮换起点，避免详情或召回预算始终被第一个来源用完。
        sources = ["follow", "related", "up_archive", "rcmd", "hot", "vertical_search"]
        offset = int(get_state("blind:source-turn", 0)) % len(sources)
        set_state("blind:source-turn", offset+1)
        for source in sources[offset:]+sources[:offset]:
            if stop and stop.is_set() or bili.rate_limited:
                return
            if source in ("hot", "rcmd", "vertical_search") and (allowed.get(source) == "off" or settings()[source] == "off"):
                continue
            if pool.is_stale(source):
                pool.expand(source, details=False, stop=stop, seed_budget=1)
            candidates = [v for v in pool.all((source,)) if not v.get("_detail_complete")]
            if candidates:
                pool.complete((source,), 2, stop=stop, candidates=candidates)

    def fetch(self, source, state, category="all", stop=None, seed_budget=None, head_only=False):
        if source == "vertical_search":
            from backend.services.vertical_search import fetch
            return fetch(stop)
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
        from backend.services.interest_profile import special_ups
        special = special_ups()
        ups = [dict(mid=up["mid"], author=up["name"]) for up in snapshot["ups"].values() if (up["regular"] or str(up["mid"]) in special) and up["mid"]]
        ups += [dict(mid=int(mid), author="") for mid in special if mid not in {str(up["mid"]) for up in ups}]
        ups, _ = filters.filter_candidates(ups)
        ups = sorted((up for up in ups if not up.get("expand_disabled") and not up.get("downrank") and up_key(up) not in cursor["used"] and (not cursor.get(up_key(up), {}).get("done") or cursor.get(up_key(up), {}).get("last_head_at", 0) <= time.time()-86400)), key=lambda v: (cursor.get(up_key(v), {}).get("last_requested_at", 0), up_key(v) not in snapshot["followings"]))
        videos = []
        for up in ups[:max(0, ARCHIVE_UPS_PER_HOUR - len(cursor["used"]))]:
            key = up_key(up)
            archive = cursor.get(key, {"page": 1, "order": "click", "done": False})
            page = bili.up_archives(up["mid"], 1 if archive["done"] else archive["page"], "pubdate" if archive["done"] else archive["order"])
            for video in page["items"]:
                video["archive_order"] = archive["order"]
            videos += page["items"]
            if archive["done"]:
                archive["last_head_at"] = time.time()
            elif archive["order"] == "click":
                archive = {"page": 1, "order": "pubdate", "done": False}
            else:
                archive["page"] += 1
                archive["done"] = not page["has_more"]
                if archive["done"]:
                    archive["last_head_at"] = time.time()
            archive["last_requested_at"] = time.time()
            cursor[key] = archive
            cursor["used"].append(key)
            if stop and stop.is_set():
                return []
            pool._save("up_archive", page["items"])
            set_state("sources:cursor:up_archive", cursor)
        return videos

    def expand_related(self, category="all", stop=None, seed_budget=None):
        cursor = get_state("sources:cursor:related", {"used": []})
        from backend.services.interest_profile import special_ups
        snapshot = affinity.snapshot()
        if "attempts" not in cursor:
            cursor["attempts"] = {bvid: {"at": time.time(), "mid": ""} for bvid in cursor["used"]}
        retryable = {key for key, up in snapshot["ups"].items() if up["regular"]} | snapshot["followings"] | special_ups()
        eligible = affinity.seeds(category, limit=1000)
        cursor["attempts"] = {bvid: value for bvid, value in cursor["attempts"].items() if bvid in {v["bvid"] for v in eligible} or value["at"] > time.time()-90*86400}
        recent_ups = {value["mid"] for value in cursor["attempts"].values() if value["at"] > time.time()-86400}
        used = {v["bvid"] for v in eligible if v["bvid"] in cursor["attempts"] and (up_key(v) not in retryable or cursor["attempts"][v["bvid"]]["at"] > time.time()-86400 or up_key(v) in recent_ups)}
        videos = []
        seeds = affinity.seeds(category, used, limit=1000)
        seeds.sort(key=lambda v: (v["bvid"] in cursor["attempts"], cursor["attempts"].get(v["bvid"], {}).get("at", 0)))
        for seed in seeds[:min(RELATED_SEEDS_PER_REFRESH, seed_budget) if seed_budget is not None else RELATED_SEEDS_PER_REFRESH]:
            items = bili.related(seed["bvid"])
            for video in items:
                video["seed_title"] = seed.get("title", "")
            videos += items
            if stop and stop.is_set():
                return []
            pool._save("related", items)
            cursor["attempts"][seed["bvid"]] = {"at": time.time(), "mid": up_key(seed)}
            cursor["used"] = sorted(cursor["attempts"])
            set_state("sources:cursor:related", cursor)
        return videos


scheduler = SourceScheduler()
