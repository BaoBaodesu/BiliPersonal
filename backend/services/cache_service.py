"""
候选池（SQLite 持久化）：
- source = hot（/x/web-interface/popular 分页）/ rcmd（首页推荐流）
- 每个 source 记录 last_refresh_at 与翻页游标；超过 POOL_TTL 从第一页重新抓取
- 旧候选超过 POOL_KEEP 清理，避免池子长期停留在旧内容
"""

import json
import threading
import time

from backend.config import POOL_KEEP, POOL_MIN_REFRESH_INTERVAL, POOL_TTL, SOURCE_TTL, SOURCE_KEEP
from backend.services.bilibili_service import BiliError, LoginExpired, RateLimited, bili
from backend.storage.database import connect, get_state, now, set_state
from backend.storage.migrate_v032 import body, merge_reasons, REASON_FIELDS
from backend.services import feed_performance as perf

SOURCES = ("hot", "rcmd", "follow", "up_archive", "related", "vertical_search")


class CandidatePool:
    def __init__(self):
        self._locks = {s: threading.Lock() for s in SOURCES}
        self.last_error = {}

    def state(self, source):
        return get_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})

    def is_stale(self, source):
        return time.time() - self.state(source)["last_refresh_at"] > SOURCE_TTL.get(source, POOL_TTL)

    @perf.measured("pool_read_ms", source_arg=1)
    def all(self, sources, clock=None):
        clock = time.time() if clock is None else clock
        perf.add("pool_read_calls")
        if not sources:
            return []
        placeholders = ",".join("?" * len(sources))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT c.data,s.source,s.reasons,s.created_at,s.last_refresh_at FROM candidates c JOIN candidate_sources s USING(bvid) WHERE s.source IN ({placeholders}) "
                "ORDER BY s.created_at DESC,s.source",
                tuple(sources),
            ).fetchall()
        perf.add("pool_rows_read", len(rows))
        seen = {}
        videos = []
        for row in rows:
            if row["last_refresh_at"] < clock - SOURCE_KEEP.get(row["source"], POOL_KEEP):
                continue
            video = perf.decode_json(row["data"])
            reasons = json.loads(row["reasons"])
            video.update({k: v for k, v in (reasons[-1] if reasons else {}).items() if k in REASON_FIELDS})
            video["source"] = row["source"]
            video["source_reasons"] = {row["source"]: reasons}
            # 旧动态接口把 pub_ts 返回为字符串，读取时兼容，不迁移已存候选。
            if row["source"] == "follow" and isinstance(video.get("pubdate"), str):
                try:
                    video["pubdate"] = float(video["pubdate"])
                except ValueError:
                    video["pubdate"] = 0
            if video["bvid"] in seen:
                seen[video["bvid"]]["source_reasons"].update(video["source_reasons"])
                seen[video["bvid"]]["sources"].append(row["source"])
                continue
            video["sources"] = [row["source"]]
            seen[video["bvid"]] = video
            videos.append(video)
        perf.observe_pool(videos)
        return videos

    def get(self, bvid):
        with connect() as conn:
            row = conn.execute("SELECT data FROM candidates WHERE bvid = ? LIMIT 1", (bvid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def count(self, source=None):
        with connect() as conn:
            if source:
                return conn.execute("SELECT COUNT(*) FROM candidate_sources WHERE source = ?", (source,)).fetchone()[0]
            return conn.execute("SELECT COUNT(DISTINCT bvid) FROM candidates").fetchone()[0]

    @perf.measured("recall_ms", source_arg=1)
    def expand(self, source, restart=False, manual=False, category="all", stop=None, details=None, seed_budget=None, head_only=False):
        """
        抓取一页新候选写入池中。返回新增条数；风控 / 其他任务正在抓取时返回 0。
        restart：从第一页重新开始（TTL 到期或手动刷新）。
        """
        from backend.services.request_coordination import current
        stop = stop or current().get("stop")
        lock = self._locks[source]
        if not perf.try_lock(lock, "source_pool_lock_busy_count"):
            return 0
        try:
            if bili.rate_limited or not bili.has_cookie:
                perf.add("backoff_skipped_count", int(bili.rate_limited))
                return 0
            state = self.state(source)
            if manual and time.time() - state["last_refresh_at"] < POOL_MIN_REFRESH_INTERVAL:
                # 手动刷新过于频繁时不重新从第一页开始，只继续向后翻页
                restart = False
            if restart or (source in ("hot", "rcmd") and self.is_stale(source)):
                state = {**state, "cursor": 1, "no_more": False, "created_at": time.time()}
            try:
                from backend.services.source_mixer import settings
                if source == "hot":
                    if state["no_more"]:
                        return 0
                    videos, no_more = bili.fetch_hot(state["cursor"], details=settings()["classic"] if details is None else details)
                    state["no_more"] = no_more
                elif source == "rcmd":
                    videos = bili.fetch_rcmd(state["cursor"], details=settings()["classic"] if details is None else details)
                else:
                    from backend.services.source_scheduler import scheduler
                    videos = scheduler.fetch(source, state, category, stop, seed_budget=seed_budget, head_only=head_only)
            except LoginExpired:
                raise
            except BiliError as e:
                self.last_error[source] = {"time": time.time(), "code": e.code}
                return 0
            if stop and stop.is_set():
                return 0
            state["cursor"] += 1
            state["last_refresh_at"] = time.time()
            set_state(f"pool:{source}", state)
            self._save(source, videos)
            print(f"候选池 {source} 新增 {len(videos)} 条")
            return len(videos)
        finally:
            lock.release()

    def _save(self, source, videos):
        t = now()
        with connect() as conn:
            for video in videos:
                existing = conn.execute("SELECT data FROM candidates WHERE bvid=?", (video["bvid"],)).fetchone()
                complete = bool(video.get("_detail_complete"))
                data = body(video)
                if existing:
                    old = json.loads(existing[0])
                    data = {**old, **data} if complete or not old.get("_detail_complete") else old
                conn.execute("INSERT INTO candidates VALUES(?,?,?,?) ON CONFLICT(bvid) DO UPDATE SET data=excluded.data,updated_at=CASE WHEN candidates.data!=excluded.data THEN excluded.updated_at ELSE candidates.updated_at END",
                             (video["bvid"], json.dumps(data, ensure_ascii=False), t, t))
                relation = conn.execute("SELECT reasons FROM candidate_sources WHERE bvid=? AND source=?", (video["bvid"], source)).fetchone()
                reasons = merge_reasons(json.loads(relation[0]) if relation else [], video, t)
                conn.execute("INSERT INTO candidate_sources VALUES(?,?,?,?,?) ON CONFLICT(bvid,source) DO UPDATE SET reasons=excluded.reasons,last_refresh_at=excluded.last_refresh_at",
                             (video["bvid"], source, json.dumps(reasons, ensure_ascii=False), t, t))
                perf.add("candidate_updated" if existing else "sync_candidates_added")
                if complete and (not existing or not json.loads(existing[0]).get("_detail_complete")):
                    perf.add("sync_detail_completed")
                if data.get("_detail_complete"):
                    queries = {reason["query"] for row in conn.execute("SELECT reasons FROM candidate_sources WHERE bvid=?", (video["bvid"],)) for reason in json.loads(row[0]) if reason.get("query")}
                    if queries:
                        existing_contributions = conn.execute("SELECT value FROM app_state WHERE key='vertical:complete'").fetchone()
                        contributions = json.loads(existing_contributions[0]) if existing_contributions else {}
                        contributions = {q: {bv: at for bv, at in values.items() if at > t-30*86400} for q, values in contributions.items()}
                        contributions = {q: values for q, values in contributions.items() if values}
                        for query in queries:
                            contributions.setdefault(query, {}).setdefault(video["bvid"], t)
                        conn.execute("INSERT INTO app_state VALUES('vertical:complete',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (json.dumps(contributions, ensure_ascii=False), t))
            conn.execute("DELETE FROM candidate_sources WHERE source=? AND last_refresh_at<?", (source, t-SOURCE_KEEP.get(source, POOL_KEEP)))
            conn.execute("DELETE FROM candidates WHERE NOT EXISTS(SELECT 1 FROM candidate_sources s WHERE s.bvid=candidates.bvid)")

    @perf.measured("complete_ms")
    def complete(self, sources, budget=12, stop=None, exclude=(), candidates=None):
        """前置过滤后有限补详情；未补全的条目不得展示。"""
        from backend.services.filter_service import filters
        from backend.services.request_coordination import current
        stop = stop or current().get("stop")
        candidates, _ = filters.filter_candidates(candidates if candidates is not None else [v for source in sources for v in self.all((source,))], record=False)
        queues = {source: [v for v in candidates if v.get("source") == source and not v.get("_detail_complete") and v["bvid"] not in exclude] for source in sources}
        completed = 0
        tried = set()
        while completed < budget and any(queues.values()):
            for source in sources:
                if completed >= budget or bili.rate_limited or (stop and stop.is_set()):
                    return completed
                if not queues[source]:
                    continue
                video = queues[source].pop(0)
                if video["bvid"] in tried:
                    continue
                tried.add(video["bvid"])
                try:
                    with perf.span("detail_complete_ms", source):
                        perf.add("detail_attempted")
                        detail = {**video, **bili.detail(video["bvid"]), "source": source, "_detail_complete": True}
                        if not stop or not stop.is_set():
                            self._save(source, [detail])
                except (RateLimited, LoginExpired):
                    perf.add("detail_failed")
                    return completed
                except BiliError as error:
                    perf.add("detail_failed")
                    self.last_error[source] = {"time": time.time(), "code": error.code}
                    completed += 1
                    continue
                if stop and stop.is_set():
                    return completed
                completed += 1
        return completed

    def clear(self):
        with connect() as conn:
            conn.execute("DELETE FROM candidates")
        for source in SOURCES:
            set_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})


pool = CandidatePool()
