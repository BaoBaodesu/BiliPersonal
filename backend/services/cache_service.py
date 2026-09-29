"""
候选池（SQLite 持久化）：
- source = hot（/x/web-interface/popular 分页）/ rcmd（首页推荐流）
- 每个 source 记录 last_refresh_at 与翻页游标；超过 POOL_TTL 从第一页重新抓取
- 旧候选超过 POOL_KEEP 清理，避免池子长期停留在旧内容
"""

import json
import threading
import time

from backend.config import POOL_KEEP, POOL_MIN_REFRESH_INTERVAL, POOL_TTL
from backend.services.bilibili_service import BiliError, LoginExpired, RateLimited, bili
from backend.storage.database import connect, get_state, now, set_state

SOURCES = ("hot", "rcmd")


class CandidatePool:
    def __init__(self):
        self._locks = {s: threading.Lock() for s in SOURCES}
        self.last_error = {}

    def state(self, source):
        return get_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})

    def is_stale(self, source):
        return time.time() - self.state(source)["last_refresh_at"] > POOL_TTL

    def all(self, sources):
        placeholders = ",".join("?" * len(sources))
        with connect() as conn:
            rows = conn.execute(
                f"SELECT data, source, created_at FROM candidates WHERE source IN ({placeholders}) "
                "ORDER BY created_at DESC",
                tuple(sources),
            ).fetchall()
        seen = set()
        videos = []
        for row in rows:
            video = json.loads(row["data"])
            if video["bvid"] in seen:
                continue
            seen.add(video["bvid"])
            videos.append(video)
        return videos

    def get(self, bvid):
        with connect() as conn:
            row = conn.execute("SELECT data FROM candidates WHERE bvid = ? LIMIT 1", (bvid,)).fetchone()
        return json.loads(row["data"]) if row else None

    def count(self, source=None):
        with connect() as conn:
            if source:
                return conn.execute("SELECT COUNT(*) FROM candidates WHERE source = ?", (source,)).fetchone()[0]
            return conn.execute("SELECT COUNT(DISTINCT bvid) FROM candidates").fetchone()[0]

    def expand(self, source, restart=False, manual=False):
        """
        抓取一页新候选写入池中。返回新增条数；风控 / 其他任务正在抓取时返回 0。
        restart：从第一页重新开始（TTL 到期或手动刷新）。
        """
        lock = self._locks[source]
        if not lock.acquire(blocking=False):
            return 0
        try:
            if bili.rate_limited or not bili.has_cookie:
                return 0
            state = self.state(source)
            if manual and time.time() - state["last_refresh_at"] < POOL_MIN_REFRESH_INTERVAL:
                # 手动刷新过于频繁时不重新从第一页开始，只继续向后翻页
                restart = False
            if restart or time.time() - state["last_refresh_at"] > POOL_TTL:
                state = {**state, "cursor": 1, "no_more": False, "created_at": time.time()}
            try:
                if source == "hot":
                    if state["no_more"]:
                        return 0
                    videos, no_more = bili.fetch_hot(state["cursor"])
                    state["no_more"] = no_more
                else:
                    videos = bili.fetch_rcmd(state["cursor"])
            except LoginExpired:
                raise
            except BiliError as e:
                self.last_error[source] = {"time": time.time(), "code": e.code}
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
                conn.execute(
                    "INSERT INTO candidates(bvid, source, data, created_at, last_refresh_at) VALUES(?, ?, ?, ?, ?) "
                    "ON CONFLICT(bvid, source) DO UPDATE SET data = excluded.data, "
                    "last_refresh_at = excluded.last_refresh_at",
                    (video["bvid"], source, json.dumps(video, ensure_ascii=False), t, t),
                )
            conn.execute("DELETE FROM candidates WHERE last_refresh_at < ?", (t - POOL_KEEP,))

    def clear(self):
        with connect() as conn:
            conn.execute("DELETE FROM candidates")
        for source in SOURCES:
            set_state(f"pool:{source}", {"last_refresh_at": 0, "created_at": 0, "cursor": 1, "no_more": False})


pool = CandidatePool()
