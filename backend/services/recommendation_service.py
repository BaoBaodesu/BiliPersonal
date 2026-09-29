"""
Feed 引擎：
Candidate Pool → 去重复 BVID → 屏蔽 UP / 关键词 / 反馈 → 排除 served → 模型 Rating → 排序 → Feed

- 每次“换一批”生成一个新的 stream；stream 按页写入 feed_cache，同一 cursor 重复请求返回相同结果（幂等）。
- served_videos 按 feed_type 记录，SERVED_WINDOW 内不重复；候选真正耗尽后才回退使用较早展示过的视频。
- 剩余候选不足时后台预取下一页候选，尽量让无限滚动不用等待。
"""

import json
import threading
import time
import traceback
import uuid

from backend.config import SERVED_WINDOW
from backend.services.bilibili_service import LoginExpired, bili
from backend.services.cache_service import pool
from backend.services.filter_service import filters
from backend.services.training_service import training
from backend.storage.database import connect, now, rows_to_dicts

FEED_SOURCES = {
    "for_you": ("hot", "rcmd"),
    "hot": ("hot",),
    "explore": ("rcmd",),
}
DEFAULT_LIMIT = 12
MAX_LIMIT = 24
# 第一页缓存多久内打开首页直接复用
CACHE_REUSE_SECONDS = 12 * 3600


class FeedError(Exception):
    pass


class RecommendationService:
    def __init__(self):
        self._gen_lock = threading.Lock()
        self._prefetching = set()
        training.on_model_changed(self._on_model_changed)

    # ---------------- 对外接口 ----------------

    def get_feed(self, feed_type, category="all", cursor=None, limit=DEFAULT_LIMIT):
        self._check_type(feed_type)
        limit = max(4, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        if cursor:
            stream_id, page = self._parse_cursor(cursor)
            return self._page(stream_id, feed_type, category, page, limit)

        # 首次打开：优先返回最近的缓存 stream 第一页
        with connect() as conn:
            row = conn.execute(
                "SELECT stream_id FROM feed_cache WHERE feed_type = ? AND category = ? AND page = 0 "
                "AND items != '[]' AND created_at > ? ORDER BY created_at DESC LIMIT 1",
                (feed_type, category, time.time() - CACHE_REUSE_SECONDS),
            ).fetchone()
        if row:
            result = self._page(row["stream_id"], feed_type, category, 0, limit)
            result["from_cache"] = True
            self.prefetch(feed_type)
            return result
        return self.refresh(feed_type, category, limit)

    def refresh(self, feed_type, category="all", limit=DEFAULT_LIMIT):
        """换一批：新建 stream"""
        self._check_type(feed_type)
        limit = max(4, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        for source in FEED_SOURCES[feed_type]:
            if pool.is_stale(source):
                self._background(lambda s=source: pool.expand(s, restart=True, manual=True), f"stale:{source}")
        stream_id = uuid.uuid4().hex[:12]
        return self._page(stream_id, feed_type, category, 0, limit)

    def explain(self, bvid):
        from backend.recommender.recommender import read_history

        video = pool.get(bvid) or self._video_from_history(bvid)
        if not video:
            raise FeedError("video not found")
        history = read_history()
        tags = set(video.get("tag") or [])
        recommender = training.recommender
        matched = recommender.known_tags(video.get("tag") or []) if recommender else [t for t in tags]
        related = []
        for h in history:
            shared = tags & set(h.get("tag") or [])
            if shared or h.get("author") == video.get("author"):
                related.append({"bvid": h["bvid"], "title": h["title"], "shared_tags": sorted(shared)[:5],
                                "same_author": h.get("author") == video.get("author")})
        related.sort(key=lambda r: (r["same_author"], len(r["shared_tags"])), reverse=True)
        return {
            "bvid": bvid,
            "matched_tags": matched[:10],
            "related_history": related[:5],
            "same_author": any(r["same_author"] for r in related),
            "source": video.get("source"),
            "rcmd_reason": video.get("rcmd_reason", ""),
        }

    def categories(self):
        """首页 Category Chips：历史分区在前，候选池分区补充"""
        from backend.recommender.recommender import read_history

        counts = {}
        for h in read_history():
            if h.get("tname"):
                counts[h["tname"]] = counts.get(h["tname"], 0) + 3
        for v in pool.all(("hot", "rcmd")):
            if v.get("tname"):
                counts[v["tname"]] = counts.get(v["tname"], 0) + 1
        zones = [z for z, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True) if z != "其他"][:10]
        return (
            [{"id": "all", "name": "全部"}]
            + [{"id": z, "name": z} for z in zones]
            + [{"id": "recent", "name": "近期兴趣"}, {"id": "discover", "name": "发现新内容"}]
        )

    def history(self, limit=50, offset=0, feed_type=None):
        sql = "SELECT * FROM recommendation_history"
        params = []
        if feed_type:
            sql += " WHERE feed_type = ?"
            params.append(feed_type)
        sql += " ORDER BY served_at DESC, rank ASC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with connect() as conn:
            rows = rows_to_dicts(conn.execute(sql, params).fetchall())
            total = conn.execute(
                "SELECT COUNT(*) FROM recommendation_history" + (" WHERE feed_type = ?" if feed_type else ""),
                [feed_type] if feed_type else [],
            ).fetchone()[0]
        return {"items": rows, "total": total}

    def debug(self):
        with connect() as conn:
            served = conn.execute("SELECT feed_type, COUNT(*) AS n FROM served_videos GROUP BY feed_type").fetchall()
            streams = conn.execute(
                "SELECT feed_type, category, COUNT(*) AS pages, MAX(created_at) AS last FROM feed_cache "
                "GROUP BY stream_id ORDER BY last DESC LIMIT 5"
            ).fetchall()
        valid = 0
        if training.recommender:
            valid = sum(1 for v in pool.all(("hot", "rcmd")) if training.recommender.known_tags(v.get("tag") or []))
        return {
            "pool": {"hot": pool.count("hot"), "rcmd": pool.count("rcmd"), "total": pool.count()},
            "pool_state": {s: pool.state(s) for s in ("hot", "rcmd")},
            "valid_candidates": valid,
            "served": {r["feed_type"]: r["n"] for r in served},
            "recent_streams": rows_to_dicts(streams),
            "rate_limit_count": bili.rate_limit_count,
            "rate_limited_until": bili.rate_limited_until,
            "recent_errors": list(bili.recent_errors),
        }

    def clear_cache(self):
        with connect() as conn:
            conn.execute("DELETE FROM feed_cache")
            conn.execute("DELETE FROM served_videos")

    def warmup(self):
        """登录后 / 启动时：后台准备候选池与首页第一页"""

        def run():
            try:
                for source in ("hot", "rcmd"):
                    if pool.count(source) < 24 or pool.is_stale(source):
                        pool.expand(source, restart=pool.is_stale(source))
                if training.recommender and not self._has_cached_first_page("for_you"):
                    self.refresh("for_you")
            except LoginExpired:
                pass
            except Exception:
                traceback.print_exc()

        self._background(run, "warmup")

    def prefetch(self, feed_type):
        """剩余可用候选不足两页时，后台再抓一页"""

        def run():
            try:
                for source in FEED_SOURCES[feed_type]:
                    if pool.is_stale(source) or self._remaining(feed_type, (source,)) < DEFAULT_LIMIT * 2:
                        pool.expand(source, restart=pool.is_stale(source))
            except LoginExpired:
                pass
            except Exception:
                traceback.print_exc()

        self._background(run, f"prefetch:{feed_type}")

    # ---------------- 分页生成 ----------------

    def _page(self, stream_id, feed_type, category, page, limit):
        with self._gen_lock:
            cached = self._cached_page(stream_id, page)
            if cached is None:
                cached = self._generate(stream_id, feed_type, category, page, limit)
        items, has_more, created_at = cached
        ranked = bool(items) and items[0].get("rating") is not None
        # 缓存页生成后用户可能又屏蔽了 UP / 关键词，返回前重新过滤
        items = self._apply_filters(items, feed_type, set(), exclude_served=False)
        return {
            "items": items,
            "next_cursor": f"{stream_id}:{page + 1}" if has_more else None,
            "has_more": has_more,
            "stream_id": stream_id,
            "generated_at": created_at,
            "from_cache": False,
            "ranked": ranked,
            "model": training.status()["model"],
            "notice": "rate_limited" if bili.rate_limited else None,
        }

    def _cached_page(self, stream_id, page):
        with connect() as conn:
            row = conn.execute(
                "SELECT items, has_more, created_at FROM feed_cache WHERE stream_id = ? AND page = ?",
                (stream_id, page),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["items"]), bool(row["has_more"]), row["created_at"]

    def _generate(self, stream_id, feed_type, category, page, limit):
        sources = FEED_SOURCES[feed_type]
        stream_bvids = self._stream_bvids(stream_id)

        ranked = self._rank(feed_type, category, sources, exclude=stream_bvids, exclude_served=True)
        # 候选不足：同步抓取新候选（每个来源最多一页）
        if len(ranked) < limit:
            for source in sources:
                try:
                    pool.expand(source)
                except LoginExpired:
                    raise
            ranked = self._rank(feed_type, category, sources, exclude=stream_bvids, exclude_served=True)
        # 候选真正耗尽：回退使用较早展示过的视频（最久未展示的优先）
        if len(ranked) < limit:
            already = {v["bvid"] for v in ranked}
            fallback = [
                v for v in self._rank(feed_type, category, sources, exclude=stream_bvids | already, exclude_served=False)
            ]
            last_served = self._last_served(feed_type)
            fallback.sort(key=lambda v: last_served.get(v["bvid"], 0))
            ranked += fallback

        items = ranked[:limit]
        has_more = len(ranked) > limit or not bili.rate_limited
        if not items:
            has_more = False
        for i, item in enumerate(items):
            item["rank"] = page * limit + i + 1
        created_at = now()
        self._record(stream_id, feed_type, category, page, items, has_more, created_at)
        self.prefetch(feed_type)
        return items, has_more, created_at

    def _rank(self, feed_type, category, sources, exclude, exclude_served):
        candidates = pool.all(sources)
        # 候选真正参与 Feed 生成时才记命中，避免预取预判污染计数
        candidates = self._apply_filters(
            candidates, feed_type, exclude, exclude_served, record_hits=True
        )
        candidates = self._apply_category(candidates, category)

        recommender = training.recommender
        if recommender is None:
            # 模型未就绪：按候选池原始顺序（热门排名 / B 站推荐顺序）返回，rating 为空
            return [self._item(v, None, []) for v in candidates]

        scored = recommender.score(candidates)
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [self._item(v, rating, tags) for v, rating, tags in scored]
        scored_bvids = {v["bvid"] for v, _, _ in scored}
        # 与训练集没有标签交集的候选：原版直接丢弃。
        # v0.2 不改模型，只在产品层保留：探索 Feed 每 3 条插入 1 条；其他 Feed 放在有评分内容之后
        unknown = [self._item(v, None, []) for v in candidates if v["bvid"] not in scored_bvids]
        if feed_type == "explore":
            mixed = []
            while ranked or unknown:
                mixed += ranked[:2]
                ranked = ranked[2:]
                if unknown:
                    mixed.append(unknown.pop(0))
            return mixed
        return ranked + unknown

    def _apply_filters(self, candidates, feed_type, exclude, exclude_served, record_hits=False):
        """统一过滤入口：精确 MID → UP 关键词 → 标题关键词，全部由 filter_service 负责。

        v0.2.1 起过滤规则来自 filter_rules，不再直接读 blocked_keywords。
        """
        hidden = self._hidden_bvids()
        served = self._served_recent(feed_type) if exclude_served else set()

        stage = []
        seen = set()
        for v in candidates:
            bvid = v["bvid"]
            if bvid in seen or bvid in exclude or bvid in served or bvid in hidden:
                continue
            seen.add(bvid)
            stage.append(v)

        kept, _stats = filters.filter_candidates(stage, record=record_hits)
        return kept

    def _apply_category(self, candidates, category):
        if not category or category == "all":
            return candidates
        if category == "recent":
            from backend.recommender.recommender import read_history

            history = sorted(read_history(), key=lambda h: h.get("view_at", 0), reverse=True)[:15]
            recent_tags = {t for h in history for t in (h.get("tag") or [])}
            recent_authors = {h.get("author") for h in history}
            return [v for v in candidates if recent_tags & set(v.get("tag") or []) or v.get("author") in recent_authors]
        if category == "discover":
            recommender = training.recommender
            if recommender is None:
                return candidates
            return [v for v in candidates if not recommender.known_tags(v.get("tag") or [])]
        return [v for v in candidates if v.get("tname") == category]

    @staticmethod
    def _item(video, rating, matched_tags):
        return {
            "bvid": video["bvid"],
            "title": video.get("title", ""),
            "pic": video.get("pic", ""),
            "author": video.get("author", ""),
            "mid": video.get("mid"),
            "face": video.get("face", ""),
            "view": video.get("view") or 0,
            "like": video.get("like") or 0,
            "duration": video.get("duration") or 0,
            "pubdate": video.get("pubdate") or 0,
            "tname": video.get("tname", ""),
            "tags": (video.get("tag") or [])[:10],
            "source": video.get("source", ""),
            "rcmd_reason": video.get("rcmd_reason", ""),
            "rating": rating,
            "matched_tags": matched_tags[:5],
        }

    # ---------------- 记录 ----------------

    def _record(self, stream_id, feed_type, category, page, items, has_more, created_at):
        ranked = bool(items) and items[0].get("rating") is not None
        with connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO feed_cache(stream_id, feed_type, category, page, items, has_more, created_at) "
                "VALUES(?, ?, ?, ?, ?, ?, ?)",
                (stream_id, feed_type, category, page, json.dumps(items, ensure_ascii=False), int(has_more), created_at),
            )
            # 旧 stream 只保留最近 3 天
            conn.execute("DELETE FROM feed_cache WHERE created_at < ?", (created_at - 3 * 86400,))
            # 模型未就绪时的临时结果不计入 served，模型就绪后可以重新参与排序
            if not ranked:
                return
            for item in items:
                conn.execute(
                    "INSERT INTO served_videos(bvid, feed_type, first_served_at, last_served_at, times) "
                    "VALUES(?, ?, ?, ?, 1) ON CONFLICT(bvid, feed_type) DO UPDATE SET "
                    "last_served_at = excluded.last_served_at, times = times + 1",
                    (item["bvid"], feed_type, created_at, created_at),
                )
                conn.execute(
                    "INSERT INTO recommendation_history(bvid, feed_type, title, author, pic, rating, rank, "
                    "model_version, served_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (item["bvid"], feed_type, item["title"], item["author"], item["pic"], item["rating"],
                     item["rank"], training.model_version, created_at),
                )

    def _stream_bvids(self, stream_id):
        with connect() as conn:
            rows = conn.execute("SELECT items FROM feed_cache WHERE stream_id = ?", (stream_id,)).fetchall()
        return {item["bvid"] for row in rows for item in json.loads(row["items"])}

    def _hidden_bvids(self):
        """被用户标记为不感兴趣 / 屏蔽 / 已看的视频，不再进入 Feed。"""
        from backend.services.feedback_service import HIDING_ACTIONS

        with connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT bvid FROM feedback WHERE action IN ({','.join('?' * len(HIDING_ACTIONS))})",
                HIDING_ACTIONS,
            ).fetchall()
        return {r["bvid"] for r in rows}

    def _served_recent(self, feed_type):
        with connect() as conn:
            rows = conn.execute(
                "SELECT bvid FROM served_videos WHERE feed_type = ? AND last_served_at > ?",
                (feed_type, time.time() - SERVED_WINDOW),
            ).fetchall()
        return {r["bvid"] for r in rows}

    def _last_served(self, feed_type):
        with connect() as conn:
            rows = conn.execute(
                "SELECT bvid, last_served_at FROM served_videos WHERE feed_type = ?", (feed_type,)
            ).fetchall()
        return {r["bvid"]: r["last_served_at"] for r in rows}

    def _remaining(self, feed_type, sources):
        candidates = self._apply_filters(pool.all(sources), feed_type, set(), True)
        recommender = training.recommender
        # 模型就绪时只统计能被模型评分的候选，避免有效候选耗尽后才补充
        if recommender:
            return sum(1 for v in candidates if recommender.known_tags(v.get("tag") or []))
        return len(candidates)

    def _has_cached_first_page(self, feed_type):
        with connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM feed_cache WHERE feed_type = ? AND page = 0 AND items != '[]' AND created_at > ? LIMIT 1",
                (feed_type, time.time() - CACHE_REUSE_SECONDS),
            ).fetchone()
        return row is not None

    # ---------------- 工具 ----------------

    def _on_model_changed(self):
        # 新模型就绪：删除未排序的临时首页缓存，让下次打开时使用模型排序结果
        with connect() as conn:
            rows = conn.execute("SELECT stream_id, items FROM feed_cache WHERE page = 0").fetchall()
            for row in rows:
                items = json.loads(row["items"])
                if items and items[0].get("rating") is None:
                    conn.execute("DELETE FROM feed_cache WHERE stream_id = ?", (row["stream_id"],))
        self.warmup()

    def _background(self, fn, key):
        if key in self._prefetching:
            return

        def run():
            try:
                fn()
            finally:
                self._prefetching.discard(key)

        self._prefetching.add(key)
        threading.Thread(target=run, name=f"bg-{key}", daemon=True).start()

    def _video_from_history(self, bvid):
        with connect() as conn:
            row = conn.execute(
                "SELECT bvid, title, author, pic FROM recommendation_history WHERE bvid = ? LIMIT 1", (bvid,)
            ).fetchone()
        if not row:
            return None
        try:
            return bili.detail(bvid)
        except Exception:
            return dict(row)

    @staticmethod
    def _check_type(feed_type):
        if feed_type not in FEED_SOURCES:
            raise FeedError(f"unknown feed type: {feed_type}")

    @staticmethod
    def _parse_cursor(cursor):
        try:
            stream_id, page = cursor.rsplit(":", 1)
            return stream_id, int(page)
        except ValueError:
            raise FeedError("invalid cursor")


recommendation = RecommendationService()
