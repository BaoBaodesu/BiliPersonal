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
import sqlite3
from contextlib import contextmanager

from backend.config import SERVED_WINDOW, FOLLOW_WINDOW, SOURCE_SYNC_DETAILS, SOURCE_DEFAULTS
from backend.services.affinity import affinity, up_key
from backend.services.source_mixer import settings, mix
from backend.services.bilibili_service import BiliError, LoginExpired, bili
from backend.services.cache_service import pool
from backend.services.filter_service import filters
from backend.services.training_service import training
from backend.storage.database import connect, now, rows_to_dicts, get_state
from backend.services import feed_performance as perf
from backend.services import request_coordination as requests_scope

FEED_SOURCES = {
    "for_you": ("hot", "rcmd"),
    "hot": ("hot",),
    "explore": ("rcmd",),
    "following": ("follow",),
}
DEFAULT_LIMIT = 12
MAX_LIMIT = 24
# 第一页缓存多久内打开首页直接复用
CACHE_REUSE_SECONDS = 12 * 3600


class FeedError(Exception):
    pass


class RecommendationService:
    def __init__(self):
        self.background_enabled = True
        self._gen_lock = threading.Lock()
        self._streams_lock = threading.Lock()
        self._stream_locks = {}
        self._prefetching = set()
        training.on_model_changed(self._on_model_changed)

    # ---------------- 对外接口 ----------------

    def get_feed(self, feed_type, category="all", cursor=None, limit=DEFAULT_LIMIT, view_id=None):
        self._check_type(feed_type)
        if cursor:
            stream_id, page = self._parse_cursor(cursor)
            return self._page(stream_id, feed_type, category, page, limit, view_id)
        return self.refresh(feed_type, category, limit, view_id)

    def refresh(self, feed_type, category="all", limit=DEFAULT_LIMIT, view_id=None):
        self._check_type(feed_type)
        limit = max(4, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
        view_id = view_id or uuid.uuid4().hex
        if not isinstance(view_id, str) or not 1 <= len(view_id) <= 128:
            raise FeedError("invalid view id")
        from backend.services.experiment_service import experiments
        # 已有批次读取无需等待别的 stream 准备候选或提交。
        with connect() as conn:
            existing = conn.execute("SELECT stream_id FROM feed_streams WHERE view_id=? AND feed_type=? AND category=?", (view_id,feed_type,category)).fetchone()
        if existing:
            return self._page(existing[0], feed_type, category, 0, limit, view_id)
        # 先持久化批次，网络失败重试也沿用分组和模型。
        with perf.lock(self._gen_lock, "gen_lock"):
            with connect() as conn:
                existing = conn.execute("SELECT stream_id FROM feed_streams WHERE view_id=? AND feed_type=? AND category=?", (view_id,feed_type,category)).fetchone()
                if existing:
                    stream_id = existing[0]
                else:
                    stream_id = uuid.uuid4().hex[:12]
                    context = experiments.context(feed_type, category, view_id)
                    conn.execute("INSERT INTO feed_streams(stream_id,view_id,feed_type,category,page_limit,model_version,experiment_id,arm,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (stream_id,view_id,feed_type,category,limit,context["model_version"],context["experiment_id"],context["arm"],now()))
                    conn.execute("INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?)", (f"stream:{stream_id}:sources",json.dumps(settings()),now()))
                    from backend.services.recommendation_policy import freeze
                    conn.execute("INSERT INTO app_state VALUES(?,?,?)", (f"stream:{stream_id}:policy", json.dumps(freeze(), ensure_ascii=False), now()))
        return self._page(stream_id, feed_type, category, 0, limit, view_id)

    def explain(self, bvid, recommendation_id=None):
        if recommendation_id:
            with connect() as conn:
                row = conn.execute("SELECT * FROM recommendation_history WHERE id=? AND bvid=?", (recommendation_id, bvid)).fetchone()
            if not row:
                raise FeedError("推荐位置不存在")
            video = json.loads(row["video_snapshot"] or "{}")
            return {"bvid": bvid, "recommendation_id": row["id"], "rating": row["rating"], "model_version": row["model_version"], "source": row["source"], "policy": video.get("_policy"), "snapshot": video, "matched_tags": [video["_policy"]["primary_theme"]] if video.get("_policy", {}).get("primary_theme") else [], "related_history": [], "same_author": False, "source_reason": self._source_reason({"source": row["source"]}), "rcmd_reason": video.get("rcmd_reason", ""), "reasons": [self._source_reason({"source": row["source"]})], "historical": True}

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
            "source_reason": video.get("source_reason") or self._source_reason(video),
            "seed_video": {"bvid": video["seed_bvid"], "title": video.get("seed_title", "")} if video.get("seed_bvid") else None,
            "up_affinity": affinity.snapshot()["ups"].get(up_key(video)),
        }

    def categories(self):
        """首页 Category Chips：历史分区在前，候选池分区补充"""
        counts = {}
        for h in affinity.history():
            if h.get("tname"):
                counts[h["tname"]] = counts.get(h["tname"], 0) + 3
        from backend.services.cache_service import SOURCES
        for v in pool.all(SOURCES):
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
        from backend.services.cache_service import SOURCES
        with connect() as conn:
            served = conn.execute("SELECT feed_type, COUNT(*) AS n FROM served_videos GROUP BY feed_type").fetchall()
            streams = conn.execute(
                "SELECT feed_type, category, COUNT(*) AS pages, MAX(created_at) AS last FROM feed_cache "
                "GROUP BY stream_id ORDER BY last DESC LIMIT 5"
            ).fetchall()
        valid = 0
        if training.recommender:
            valid = sum(1 for v in pool.all(SOURCES) if
                        (all(v.get(k) is not None for k in ("view","like","favorite")) if str(training.model_version).startswith("ranker-v03-")
                         else training.recommender.known_tags(v.get("tag") or [])))
        return {
            "pool": {**{source: pool.count(source) for source in SOURCES}, "total": pool.count()},
            "pool_state": {s: pool.state(s) for s in SOURCES},
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
            conn.execute("DELETE FROM app_state WHERE key LIKE 'stream:%:page:%:preparation'")

    def warmup(self):
        """登录后 / 启动时：后台准备候选池与首页第一页"""
        if not self.background_enabled or not bili.has_cookie:
            return
        from backend.services.source_scheduler import scheduler
        scheduler.start()
        if not settings()["classic"]:
            from backend.services.model_registry import registry
            scheduler.request_refill("for_you", "all", DEFAULT_LIMIT, settings(), registry.state().get("active") or "fallback-v03")
            return

        def run():
            try:
                for source in ("hot", "rcmd"):
                    if pool.count(source) < 24 or pool.is_stale(source):
                        pool.expand(source, restart=pool.is_stale(source))
            except LoginExpired:
                pass
            except Exception:
                traceback.print_exc()

        self._background(run, "warmup")

    def prefetch(self, feed_type, context=None, category="all", limit=DEFAULT_LIMIT):
        """剩余可用候选不足两页时，后台再抓一页"""
        if not self.background_enabled or not bili.has_cookie:
            return
        if feed_type == "following" or not (context or {}).get("sources_settings", settings())["classic"]:
            from backend.services.source_scheduler import scheduler
            from backend.services.model_registry import registry
            scheduler.request_refill(feed_type, category, limit, (context or {}).get("sources_settings", settings()), (context or {}).get("model_version", registry.state().get("active") or "fallback-v03"))
            return

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

    def _page(self, stream_id, feed_type, category, page, limit, view_id=None):
        with connect() as conn:
            row = conn.execute("SELECT * FROM feed_streams WHERE stream_id=?", (stream_id,)).fetchone()
        if not row or row["feed_type"] != feed_type or row["category"] != category or page < 0 or (view_id and row["view_id"] != view_id):
            raise FeedError("invalid or expired stream")
        context = dict(row)
        from backend.services.source_scheduler import scheduler
        context["request_stop"] = scheduler._stop
        context["sources_settings"] = get_state(f"stream:{stream_id}:sources", {**SOURCE_DEFAULTS, "classic": True})
        if perf.current():
            perf.current().info.update(feed_type=feed_type, category=category, page=page, limit=row["page_limit"],
                                       mode="classic" if context["sources_settings"]["classic"] else "mixed",
                                       model_version=context["model_version"], sources_settings=context["sources_settings"],
                                       rate_limited_at_start=bili.rate_limited,
                                       backoff_remaining_ms=max(0, bili.rate_limited_until - time.time()) * 1000)
        from backend.services.model_registry import registry
        ended = registry.state().get("last_trial")
        if context["experiment_id"] and (not ended or ended["id"] != context["experiment_id"]):
            path = registry.root / "experiments" / context["experiment_id"] / "trial.json"
            if path.exists():
                ended = json.loads(path.read_text(encoding="utf-8"))
        if context["experiment_id"] and context["arm"] == "candidate" and ended and ended["id"] == context["experiment_id"] and ended["status"] in ("stopped","rejected"):
            raise FeedError("在线试用已停止，请换一批继续使用当前模型")
        limit = row["page_limit"]
        cached = self._cached_page(stream_id, page)
        if perf.current():
            perf.current().info["feed_page_cache_hit"] = cached is not None
        if cached is None:
            with self._stream_guard(stream_id):
                cached = self._cached_page(stream_id, page)
                if cached is None:
                    try:
                        cached = self._generate(stream_id, feed_type, category, page, limit, context)
                    except sqlite3.IntegrityError:
                        cached = self._cached_page(stream_id, page)
                        if cached is None:
                            raise
        items, has_more, created_at = cached
        ranked = bool(items) and items[0].get("rating") is not None
        if any(v.get("replay") for v in items):
            from backend.services.interest_profile import preferences
            permitted = preferences()
            with connect() as conn:
                newly_watched = {r[0] for r in conn.execute("SELECT bvid FROM history_events WHERE watched_at>=? UNION SELECT bvid FROM feedback WHERE action='watched' AND revoked_at IS NULL AND created_at>=?", (created_at, created_at))}
            # 撤销回看许可或新发生已看行为时，旧缓存也立即停止绕过已看排除。
            items = [v for v in items if not v.get("replay") or permitted.get(v["bvid"], {}).get("allow_replay") and v["bvid"] not in newly_watched]
        # 缓存页生成后用户可能又屏蔽了 UP / 关键词，返回前重新过滤
        items = self._apply_filters(items, feed_type, set(), exclude_served=False, replay={v["bvid"] for v in items if v.get("replay")})
        perf.returned(items)
        if perf.current():
            perf.current().info["rate_limited_at_end"] = bili.rate_limited
        result = {
            "items": items,
            "next_cursor": f"{stream_id}:{page + 1}" if has_more else None,
            "has_more": has_more,
            "stream_id": stream_id,
            "generated_at": created_at,
            "from_cache": False,
            "ranked": ranked,
            "model": "ready" if context["model_version"] != "fallback-v03" else "none",
            "model_version": context["model_version"],
            "view_id": context["view_id"],
            "notice": "rate_limited" if bili.rate_limited else "accumulating" if not items and not context["sources_settings"]["classic"] else None,
        }
        if feed_type == "following":
            result["following_freshness"] = get_state(f"stream:{stream_id}:page:0:preparation", {"status": "stale", "checked_at": None})
            if result["following_freshness"]["status"] == "stale" and result["notice"] != "rate_limited":
                result["notice"] = "following_stale"
            elif len(items) < limit and result["notice"] is None:
                result["notice"] = "accumulating"
        elif not context["sources_settings"]["classic"] and len(items) < limit and result["notice"] is None:
            result["notice"] = "accumulating"
        return result

    @contextmanager
    def _stream_guard(self, stream_id):
        with self._streams_lock:
            state = self._stream_locks.setdefault(stream_id, [threading.Lock(), 0])
            state[1] += 1
        try:
            with perf.lock(state[0], "stream_lock"):
                yield
        finally:
            with self._streams_lock:
                state[1] -= 1
                if not state[1]:
                    del self._stream_locks[stream_id]

    def _cached_page(self, stream_id, page):
        with connect() as conn:
            row = conn.execute(
                "SELECT items, has_more, created_at FROM feed_cache WHERE stream_id = ? AND page = ?",
                (stream_id, page),
            ).fetchone()
        if not row:
            return None
        return json.loads(row["items"]), bool(row["has_more"]), row["created_at"]

    def _generate(self, stream_id, feed_type, category, page, limit, context):
        if feed_type == "following" or not context.get("sources_settings", settings())["classic"]:
            return self._generate_mixed(stream_id, feed_type, category, page, limit, context)
        from backend.services.model_registry import registry
        bundle = registry.load(context["model_version"])
        sources = FEED_SOURCES[feed_type]
        stream_bvids = self._stream_bvids(stream_id)
        if any(v.get("_detail_complete") is False for v in pool.all(sources)):
            pool.complete(sources, SOURCE_SYNC_DETAILS)

        ranked = self._rank(feed_type, category, sources, exclude=stream_bvids, exclude_served=True, bundle=bundle, record_hits=False)
        # 候选不足：同步抓取新候选（每个来源最多一页）
        if len(ranked) < limit:
            for source in sources:
                try:
                    pool.expand(source)
                except LoginExpired:
                    raise
            ranked = self._rank(feed_type, category, sources, exclude=stream_bvids, exclude_served=True, bundle=bundle, record_hits=False)
        with perf.lock(self._gen_lock, "gen_lock"):
            if context.get("request_stop") and context["request_stop"].is_set():
                raise FeedError("候选准备已停止，请重新登录或换一批")
            cached = self._cached_page(stream_id, page)
            if cached is not None:
                return cached
            stream_bvids = self._stream_bvids(stream_id)
            ranked = self._rank(feed_type, category, sources, exclude=stream_bvids, exclude_served=True, bundle=bundle)
            # 候选真正耗尽：回退使用较早展示过的视频（最久未展示的优先）
            if len(ranked) < limit:
                already = {v["bvid"] for v in ranked}
                fallback = [
                    v for v in self._rank(feed_type, category, sources, exclude=stream_bvids | already, exclude_served=False, bundle=bundle)
                ]
                last_served = self._last_served(feed_type)
                fallback.sort(key=lambda v: last_served.get(v["bvid"], 0))
                ranked += fallback

            items = ranked[:limit]
            if bundle and getattr(bundle, "policy", "base") == "diversity" and feed_type == "for_you" and category == "all":
                from backend.services.ranking_policy import diversify
                items = diversify(ranked, limit, self._stream_authors(stream_id))
            has_more = len(ranked) > limit or not bili.rate_limited
            if not items:
                has_more = False
            for i, item in enumerate(items):
                item["rank"] = page * limit + i + 1
            created_at = now()
            self._record(stream_id, feed_type, category, page, items, has_more, created_at, context)
        self.prefetch(feed_type)
        return items, has_more, created_at

    def _generate_mixed(self, stream_id, feed_type, category, page, limit, context):
        from backend.services.model_registry import registry
        bundle = registry.load(context["model_version"])
        from backend.services.recommendation_policy import freeze
        options = dict(context["sources_settings"])
        options["_policy"] = get_state(f"stream:{stream_id}:policy", {"enabled": False})
        sources = self._mixed_sources(feed_type, options)
        if perf.current() and perf.current().deep:
            for source in sources:
                pool.all((source,))
            perf.current().baseline_frozen = True
        if perf.current():
            for source in perf.SOURCES:
                if source not in sources:
                    perf.current().sources[source]["status"] = "disabled"
        excluded = self._stream_bvids(stream_id)
        if page == 0 and feed_type == "following":
            self._prepare_following(stream_id, limit, excluded, context.get("request_stop"))
        with perf.lock(self._gen_lock, "gen_lock"):
            if context.get("request_stop") and context["request_stop"].is_set():
                raise FeedError("候选准备已停止，请重新登录或换一批")
            cached = self._cached_page(stream_id, page)
            if cached is not None:
                return cached
            excluded = self._stream_bvids(stream_id)
            ranked = self.rank_sources(self._mixed_candidates(stream_id, feed_type, sources, excluded), feed_type, category, excluded, bundle, affinity.snapshot(), policy=options["_policy"] if options["_policy"].get("version") else freeze(False))
            items = self._select_mixed(ranked, feed_type, sources, limit, options)
            remaining = {source: [v for v in videos if v["bvid"] not in {item["bvid"] for item in items}] for source, videos in ranked.items()}
            has_more = bool(items) and bool(self._select_mixed(remaining, feed_type, sources, limit, options))
            for i, item in enumerate(items):
                item["rank"] = page * limit + i + 1
            created_at = now()
            self._record(stream_id, feed_type, category, page, items, has_more, created_at, context, recheck_cooldown=feed_type != "following")
        self.prefetch(feed_type, context=context, category=category, limit=limit)
        return items, has_more, created_at

    @staticmethod
    def _mixed_sources(feed_type, options):
        sources = ("follow", "related", "up_archive") + (("vertical_search",) if options.get("vertical_search", "off") != "off" else ()) + tuple(s for s in ("rcmd", "hot") if options[s] != "off") if feed_type == "for_you" else FEED_SOURCES[feed_type]
        if options.get("_policy", {}).get("enabled") and options["_policy"]["settings"]["archive"] == "off":
            sources = tuple(s for s in sources if s != "up_archive")
        return () if feed_type in ("hot", "explore") and options[sources[0]] == "off" else sources

    def ready_buffer(self, feed_type, category, limit, options, model_version, pages=3):
        """后台模拟连续页面，按实际 Mixer 结果统计储备，不写命中或曝光。"""
        from backend.services.model_registry import registry
        from backend.services.recommendation_policy import freeze
        options = {**options, "_policy": options.get("_policy") or freeze()}
        candidates = pool.all(self._mixed_sources(feed_type, options))
        if feed_type == "following":
            candidates, _ = filters.filter_candidates(candidates, record=False)
            candidates.sort(key=lambda v: v.get("pubdate") or 0, reverse=True)
            candidates = candidates[:next((i for i, v in enumerate(candidates) if v.get("_detail_complete") is not True), len(candidates))]
        snapshot, excluded, counts = affinity.snapshot(), set(), {}
        bundle = registry.load(model_version)
        for page in range(pages):
            ranked = self.rank_sources(candidates, feed_type, category, excluded, bundle, snapshot, record_hits=False, policy=options["_policy"])
            items = self._select_mixed(ranked, feed_type, self._mixed_sources(feed_type, options), limit, options)
            for item in items:
                counts[item["source"]] = counts.get(item["source"], 0) + 1
            if len(items) < limit:
                return page, counts
            excluded.update(v["bvid"] for v in items)
        return pages, counts

    def _mixed_candidates(self, stream_id, feed_type, sources, excluded):
        candidates = pool.all(sources)
        if feed_type != "following":
            return candidates
        metadata = get_state(f"stream:{stream_id}:page:0:preparation", {})
        if metadata.get("status") != "checked":
            return candidates
        # 只消费本次检查时的时间线，缺详情处不能用更旧视频越过。
        candidates, _ = filters.filter_candidates([v for v in candidates if v["bvid"] not in excluded and (v.get("pubdate") or 0) <= metadata["checked_at"]], record=False)
        candidates.sort(key=lambda v: v.get("pubdate") or 0, reverse=True)
        for index, video in enumerate(candidates):
            if video.get("_detail_complete") is not True:
                return candidates[:index]
        return candidates

    def _prepare_following(self, stream_id, limit, excluded, stop=None):
        key = f"stream:{stream_id}:page:0:preparation"
        if get_state(key):
            return
        metadata = {"status": "stale", "checked_at": None}
        try:
            with requests_scope.scope(total=4, details=3, recalls=1, timeout=5, stop=stop, candidate=True) as request:
                if bili.rate_limited or not bili.has_cookie:
                    raise requests_scope.BudgetEnded("rate_limited" if bili.rate_limited else "logged_out")
                page = bili.follow_feed()
                requests_scope.check(request, bili.rate_limited, sending=False)
                pool._save("follow", page["items"])
                # 只更新成功 head 的刷新时间，不覆盖后台独立维护的游标。
                with connect() as conn:
                    conn.execute("INSERT INTO app_state(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=json_set(app_state.value,'$.last_refresh_at',json_extract(excluded.value,'$.last_refresh_at')),updated_at=excluded.updated_at",
                                 ("pool:follow", json.dumps({**pool.state("follow"), "last_refresh_at": time.time()}), now()))
                metadata = {"status": "checked", "checked_at": time.time()}
                # 先保存成功检查，生成失败后的同 view 重试不再重复访问 head。
                from backend.storage.database import set_state
                set_state(key, metadata)
                tried = set()
                request["budget"]["details"] = min(3, max(0, limit - len(self._mixed_candidates(stream_id, "following", ("follow",), excluded))))
                request["budget"]["total"] = min(request["budget"]["total"], request["budget"]["details"])
                while len(self._mixed_candidates(stream_id, "following", ("follow",), excluded)) < limit:
                    candidates, _ = filters.filter_candidates([v for v in pool.all(("follow",)) if v["bvid"] not in excluded and (v.get("pubdate") or 0) <= metadata["checked_at"]], record=False)
                    candidates.sort(key=lambda v: v.get("pubdate") or 0, reverse=True)
                    pending = next((v for v in candidates if v.get("_detail_complete") is not True), None)
                    if not pending or pending["bvid"] in tried or (pending["bvid"] not in bili._detail_cache and sum(bvid not in bili._detail_cache for bvid in tried) >= 3):
                        break
                    tried.add(pending["bvid"])
                    pool.complete(("follow",), 1, candidates=[pending], stop=stop)
        except (BiliError, requests_scope.BudgetEnded) as error:
            metadata["reason"] = str(getattr(error, "code", error))[:80]
        from backend.storage.database import set_state
        set_state(key, metadata)

    @staticmethod
    def _select_mixed(ranked, feed_type, sources, limit, options):
        if feed_type == "following":
            return mix(ranked, limit, options, following=True)
        elif feed_type == "for_you":
            return mix(ranked, limit, options)
        else:
            videos = ([v for v in ranked.get(sources[0], []) if not v.get("downrank")] + [v for v in ranked.get(sources[0], []) if v.get("downrank")][:1]) if sources else []
            if not options.get("_policy", {}).get("enabled"):
                return videos[:limit]
            selected, authors = [], set()
            for video in videos:
                if up_key(video) in authors or video.get("replay") and any(v.get("replay") for v in selected):
                    continue
                authors.add(up_key(video))
                selected.append(video)
                if len(selected) == limit:
                    break
            return selected

    @perf.measured("rank_sources_ms")
    def rank_sources(self, candidates, feed_type, category, exclude, bundle, snapshot=None, record_hits=True, policy=None, cooled=None):
        """线上和盲评共用同一过滤、来源排序与混合口径。"""
        snapshot = snapshot or affinity.snapshot()
        from backend.services import interest_profile, recommendation_policy
        policy = policy or recommendation_policy.freeze()
        special = set(policy["special_ups"])
        replay = interest_profile.replay_eligible(snapshot) if feed_type != "following" else {}
        unique = {}
        for video in candidates:
            if video.get("_detail_complete") is not True or video["bvid"] in exclude:
                continue
            if video["bvid"] not in unique:
                unique[video["bvid"]] = {**video, "sources": [], "source_reasons": {}}
            unique[video["bvid"]]["sources"] += [source for source in video.get("sources", [video["source"]]) if source not in unique[video["bvid"]]["sources"]]
            unique[video["bvid"]]["source_reasons"].update(video.get("source_reasons", {}))
        candidates = list(unique.values())
        if feed_type == "following":
            candidates, _ = filters.filter_candidates(candidates, record=record_hits)
        else:
            candidates = self._apply_filters(candidates, feed_type, exclude, False, record_hits, replay=replay)
            cooled = self._cooled_bvids() if cooled is None else cooled
            for video in candidates:
                if "vertical_search" in video["sources"]:
                    from backend.services.vertical_search import eligible_reasons
                    video["source_reasons"]["vertical_search"] = eligible_reasons(video["source_reasons"].get("vertical_search", []), policy["profile"]["config"], special)
                    if not video["source_reasons"]["vertical_search"]:
                        video["sources"].remove("vertical_search")
                video["sources"] = [source for source in video["sources"] if (video["bvid"], source) not in cooled and
                                    (source != "follow" or video.get("pubdate", 0) >= time.time()-FOLLOW_WINDOW and up_key(video) in snapshot["followings"]) and
                                    (source != "up_archive" or (snapshot["ups"].get(up_key(video), {}).get("regular") or up_key(video) in special))]
            candidates = [v for v in candidates if v["sources"] and (v["bvid"] not in snapshot["watched"] or v["bvid"] in replay)]
            candidates = affinity.quality_gate(candidates, snapshot)
            candidates = self._apply_category(candidates, category, bundle)
            for video in candidates:
                if video["bvid"] in snapshot["watched"] and video["bvid"] in replay:
                    video["replay"] = replay[video["bvid"]]
        with perf.span("rank_ms"):
            scores = {v["bvid"]: (rating, tags) for v, rating, tags in bundle.score(candidates)} if bundle and feed_type != "following" else {}
        result = {v["source"]: [] for v in unique.values()}
        for source in {source for v in candidates for source in v["sources"]}:
            videos = [{**v, "source": source} for v in candidates if source in v["sources"]]
            for video in videos:
                reasons = video["source_reasons"].get(source, [])
                if source in video["source_reasons"]:
                    from backend.storage.migrate_v032 import REASON_FIELDS
                    for field in REASON_FIELDS:
                        video.pop(field, None)
                    video.update({k: v for k, v in reasons[-1].items() if k in REASON_FIELDS})
            if source == "follow":
                videos.sort(key=lambda v: v.get("pubdate") or 0, reverse=True)
            elif source == "up_archive":
                videos.sort(key=lambda v: (v.get("archive_order") != "click", -(v.get("view") or 0) if v.get("archive_order") == "click" else -(v.get("pubdate") or 0)))
            result[source] = [self._item(v, *scores.get(v["bvid"], (None, []))) for v in videos]
            if policy["enabled"] and feed_type != "following":
                result[source] = [recommendation_policy.annotate(v, policy, snapshot) for v in result[source]]
            result[source].sort(key=lambda v: (bool(v.get("downrank")), v["rating"] is None, -(v.get("rule_score", v["rating"]) or 0)))
        return result

    @perf.measured("cooldown_ms")
    def _cooled_bvids(self):
        with connect() as conn:
            rows = conn.execute("SELECT r.bvid,r.source,r.served_at,r.clicked FROM recommendation_history r JOIN served_videos s ON s.bvid=r.bvid AND s.feed_type=r.feed_type WHERE r.feed_type!='following' AND r.source IS NOT NULL ORDER BY r.served_at,r.id").fetchall()
        streaks = {}
        for row in rows:
            key = (row["bvid"], row["source"])
            count, last = streaks.get(key, (0, 0))
            if row["source"] in ("up_archive", "related", "vertical_search") and row["served_at"] - last >= 30 * 86400:
                count = 0
            streaks[key] = (0 if row["clicked"] else count + 1, row["served_at"])
        from backend.services.cache_service import SOURCES
        floor = {(row["bvid"], source) for row in rows if row["served_at"] > time.time()-1800 for source in SOURCES}
        return floor | {key for key, (count, last) in streaks.items() if
                (key[1] in ("follow", "hot", "rcmd") and (last > time.time() - SERVED_WINDOW or count >= 2)) or
                (key[1] in ("up_archive", "related", "vertical_search") and count >= 2 and last > time.time() - 30 * 86400)}

    @staticmethod
    def _source_reason(video):
        return {"follow": "来自关注 UP 的新作", "up_archive": "来自常看 UP 的旧作", "related": "与你喜欢的视频相关", "rcmd": "来自 B 站推荐流", "hot": "来自热门榜"}.get(video.get("source"), "")

    @perf.measured("rank_ms")
    def _rank(self, feed_type, category, sources, exclude, exclude_served, bundle=None, record_hits=True):
        candidates = [v for v in pool.all(sources) if v.get("_detail_complete") is not False]
        # 候选真正参与 Feed 生成时才记命中，避免预取预判污染计数
        candidates = self._apply_filters(
            candidates, feed_type, exclude, exclude_served, record_hits=record_hits
        )
        candidates = self._apply_category(candidates, category, bundle)

        recommender = bundle
        if recommender is None:
            # 模型未就绪：按候选池原始顺序（热门排名 / B 站推荐顺序）返回，rating 为空
            return [self._item(v, None, []) for v in candidates]

        scored = recommender.score(candidates)
        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = [self._item(v, rating, tags) for v, rating, tags in scored]
        scored_bvids = {v["bvid"] for v, _, _ in scored}
        # 未评分内容保留；探索流每三条放一条未知标签内容，v0.3 的未知内容仍保留评分。
        unknown = [self._item(v, None, []) for v in candidates if v["bvid"] not in scored_bvids]
        if feed_type == "explore":
            unknown = [v for v in ranked if not v["matched_tags"]] + unknown
            ranked = [v for v in ranked if v["matched_tags"]]
            mixed = []
            while ranked or unknown:
                mixed += ranked[:2]
                ranked = ranked[2:]
                if unknown:
                    mixed.append(unknown.pop(0))
            return mixed
        return ranked + unknown

    def _apply_filters(self, candidates, feed_type, exclude, exclude_served, record_hits=False, dedupe_source=False, replay=()):
        """统一过滤入口：精确 MID → UP 关键词 → 标题关键词，全部由 filter_service 负责。

        v0.2.1 起过滤规则来自 filter_rules，不再直接读 blocked_keywords。
        """
        hidden = self._hidden_bvids() if feed_type != "following" else set()
        if replay:
            with connect() as conn:
                hard = {r[0] for r in conn.execute("SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ('not_interested','block_up')")}
            hidden = (hidden-set(replay)) | hard
        served = self._served_recent(feed_type) if exclude_served else set()

        stage = []
        seen = set()
        for v in candidates:
            bvid = v["bvid"]
            key = (bvid, v.get("source")) if dedupe_source else bvid
            if key in seen or bvid in exclude or bvid in served or bvid in hidden:
                continue
            seen.add(key)
            stage.append(v)

        kept, _stats = filters.filter_candidates(stage, record=record_hits)
        return kept

    def _apply_category(self, candidates, category, bundle=None):
        if not category or category == "all":
            return candidates
        if category == "recent":
            history = sorted(affinity.history(), key=lambda h: h.get("view_at", 0) or h.get("observed_at", 0), reverse=True)[:15]
            recent_tags = {t for h in history for t in (h.get("tag") or [])}
            recent_authors = {h.get("author") for h in history}
            return [v for v in candidates if recent_tags & set(v.get("tag") or []) or v.get("author") in recent_authors]
        if category == "discover":
            recommender = bundle
            if recommender is None:
                return candidates
            return [v for v in candidates if not recommender.known_tags(v.get("tag") or [])]
        return [v for v in candidates if v.get("tname") == category]

    @staticmethod
    def _item(video, rating, matched_tags):
        return {
            "_snapshot": dict(video),
            "replay": video.get("replay"),
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
            "source_reason": RecommendationService._source_reason(video),
            "seed_bvid": video.get("seed_bvid"),
            "seed_title": video.get("seed_title"),
            "downrank": video.get("downrank", False),
            "stranger": video.get("stranger", False),
            "rating": rating,
            "matched_tags": matched_tags[:5],
        }

    # ---------------- 记录 ----------------

    @perf.measured("record_ms")
    def _record(self, stream_id, feed_type, category, page, items, has_more, created_at, context, recheck_cooldown=False):
        from backend.services.evaluation_service import evaluation
        predictions = evaluation.predict([v["_snapshot"] for v in items])
        with connect() as conn:
            if recheck_cooldown:
                conn.execute("BEGIN IMMEDIATE")
                cooled = self._cooled_bvids()
                items[:] = [v for v in items if (v["bvid"], v["source"]) not in cooled]
            for item in items:
                snapshot = item.pop("_snapshot")
                if item.get("_policy"):
                    snapshot["_policy"] = {**item["_policy"], "primary_source": item.get("source"), "primary_theme": item["_policy"].get("theme"), "primary_query": snapshot.get("query"), "assisted_sources": snapshot.get("sources", []), "source_reasons": snapshot.get("source_reasons", {})}
                else:
                    snapshot["_policy"] = {"version": "p0-v032", "primary_source": item.get("source"), "primary_query": snapshot.get("query"), "source_reasons": snapshot.get("source_reasons", {})}
                conn.execute("INSERT INTO served_videos(bvid,feed_type,first_served_at,last_served_at,times) VALUES(?,?,?,?,1) ON CONFLICT(bvid,feed_type) DO UPDATE SET last_served_at=excluded.last_served_at,times=times+1", (item["bvid"],feed_type,created_at,created_at))
                result = conn.execute("INSERT INTO recommendation_history(bvid,feed_type,title,author,pic,rating,rank,model_version,served_at,stream_id,page,view_id,experiment_id,arm,video_snapshot,predictions,source) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (item["bvid"],feed_type,item["title"],item["author"],item["pic"],item["rating"],item["rank"],context["model_version"],created_at,stream_id,page,context["view_id"],context["experiment_id"],context["arm"],json.dumps(snapshot,ensure_ascii=False),json.dumps(predictions.get(item["bvid"],{})),item.get("source")))
                item.update(recommendation_id=result.lastrowid, view_id=context["view_id"], model_version=context["model_version"])
            conn.execute("INSERT INTO feed_cache(stream_id,feed_type,category,page,items,has_more,created_at,model_version,page_limit,view_id,experiment_id,arm) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                         (stream_id,feed_type,category,page,json.dumps(items,ensure_ascii=False),int(has_more),created_at,context["model_version"],context["page_limit"],context["view_id"],context["experiment_id"],context["arm"]))
            conn.execute("DELETE FROM feed_cache WHERE created_at<?", (created_at-3*86400,))
            conn.execute("DELETE FROM app_state WHERE key LIKE 'stream:%:page:%:preparation' AND updated_at<?", (created_at-3*86400,))

    def _stream_bvids(self, stream_id):
        with connect() as conn:
            rows = conn.execute("SELECT items FROM feed_cache WHERE stream_id = ?", (stream_id,)).fetchall()
        return {item["bvid"] for row in rows for item in json.loads(row["items"])}

    def _stream_authors(self, stream_id):
        with connect() as conn:
            rows = conn.execute("SELECT items FROM feed_cache WHERE stream_id=? ORDER BY page", (stream_id,)).fetchall()
        return [str(v.get("mid") or v.get("author")) for r in rows for v in json.loads(r[0])]

    def _hard_hidden_bvids(self):
        with connect() as conn:
            return {r[0] for r in conn.execute("SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ('not_interested','block_up')")}

    def _hidden_bvids(self):
        """被用户标记为不感兴趣 / 屏蔽 / 已看的视频，不再进入 Feed。"""
        from backend.services.feedback_service import HIDING_ACTIONS

        with connect() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ({','.join('?' * len(HIDING_ACTIONS))})",
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
            if str(training.model_version).startswith("ranker-v03-"):
                return sum(all(v.get(k) is not None for k in ("view","like","favorite")) for v in candidates)
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
        # 已发出的 stream 保留版本，只为新浏览准备候选。
        self.warmup()

    def _background(self, fn, key):
        if not self.background_enabled or not bili.has_cookie:
            return
        if key in self._prefetching:
            return

        from backend.services.source_scheduler import scheduler
        stop = scheduler._stop
        def run():
            try:
                with requests_scope.scope(background=True, candidate=True, stop=stop):
                    if not stop.is_set() and self.background_enabled:
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
