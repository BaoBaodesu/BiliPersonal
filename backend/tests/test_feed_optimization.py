"""Feed 优化回归，候选与接口均为本地测试数据。"""
import unittest
import time
import threading
import sqlite3
from collections import OrderedDict
from contextlib import contextmanager
from unittest.mock import patch

from backend.tests import test_v031 as fixtures


class FeedOptimizationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.V031Test("test_default_quota_and_native_share")
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_ready_and_redistribution_need_no_network(self):
        for i in range(20):
            fixtures.record_history(fixtures.video("fav-" + str(i), mid=i + 1, source="favorite", fav_time=time.time()))
        self.fixture.save([fixtures.video(str(i), mid=i + 1) for i in range(20)])
        with patch.object(fixtures.pool, "complete") as complete, patch.object(fixtures.pool, "expand") as expand:
            result = fixtures.rs.recommendation.refresh("for_you", view_id="ready")
        self.assertEqual(len(result["items"]), 12)
        complete.assert_not_called()
        expand.assert_not_called()

    def test_short_page_ends_and_new_batch_can_recover(self):
        self.fixture.save([fixtures.video("one")])
        first = fixtures.rs.recommendation.refresh("for_you", view_id="short")
        self.assertEqual(len(first["items"]), 1)
        self.assertFalse(first["has_more"])
        self.fixture.save([fixtures.video(str(i), mid=i + 10) for i in range(20)])
        for i in range(20):
            fixtures.record_history(fixtures.video("fav-" + str(i), mid=i + 10, source="favorite", fav_time=time.time()))
        self.assertEqual(first, fixtures.rs.recommendation.refresh("for_you", view_id="short"))
        self.assertEqual(len(fixtures.rs.recommendation.refresh("for_you", view_id="new")["items"]), 12)

    def test_following_latest_prefix_budget_and_retry(self):
        self.fixture.save([fixtures.video("old", source="follow", pubdate=time.time()-100)])
        raw = [fixtures.video(str(i), source="follow", pubdate=time.time()-i, _detail_complete=False) for i in range(7)]
        with patch.object(fixtures.bili, "follow_feed", return_value={"items": raw}) as head, patch.object(fixtures.bili, "_detail_cache", OrderedDict()), patch.object(fixtures.bili, "detail", side_effect=lambda bvid: next(dict(v, _detail_complete=True) for v in raw if v["bvid"] == bvid)) as detail:
            first = fixtures.rs.recommendation.refresh("following", view_id="latest")
            second = fixtures.rs.recommendation.refresh("following", view_id="latest")
        self.assertEqual([v["bvid"] for v in first["items"]], ["0", "1", "2"])
        self.assertFalse(first["has_more"])
        self.assertEqual(first, second)
        self.assertEqual(head.call_count, 1)
        self.assertEqual(detail.call_count, 3)
        self.assertEqual(first["following_freshness"]["status"], "checked")

    def test_following_failure_is_explicit_local_fallback(self):
        self.fixture.save([fixtures.video("old", source="follow", pubdate=time.time()-100)])
        with patch.object(fixtures.bili, "follow_feed", side_effect=fixtures.BiliError("network")):
            result = fixtures.rs.recommendation.refresh("following", view_id="failed")
        self.assertEqual(result["items"][0]["bvid"], "old")
        self.assertEqual(result["following_freshness"]["status"], "stale")
        self.assertEqual(result["notice"], "following_stale")

    def test_category_preserves_short_page_without_foreground_details(self):
        for i in range(15):
            fixtures.record_history(fixtures.video("fav-" + str(i), mid=i + 1, source="favorite", fav_time=time.time()))
        self.fixture.save([fixtures.video(str(i), mid=i+1, _detail_complete=i<11) for i in range(15)])
        with patch.object(fixtures.bili, "detail", side_effect=lambda bvid: fixtures.video(bvid, mid=int(bvid)+1)) as detail, patch.object(fixtures.pool, "expand") as expand:
            result = fixtures.rs.recommendation.refresh("for_you", category="科技", view_id="deficit")
        self.assertEqual(len(result["items"]), 11)
        self.assertEqual(detail.call_count, 0)
        expand.assert_not_called()

    def test_following_unready_failure_does_not_skip_to_old(self):
        self.fixture.save([fixtures.video("old", source="follow", pubdate=time.time()-100)])
        with patch.object(fixtures.bili, "follow_feed", return_value={"items": [fixtures.video("new", source="follow", _detail_complete=False)]}), patch.object(fixtures.bili, "detail", side_effect=fixtures.BiliError("network")):
            result = fixtures.rs.recommendation.refresh("following", view_id="gap")
        self.assertEqual(result["items"], [])
        self.assertFalse(result["has_more"])

    def test_background_low_water_uses_real_ready_and_limits_work(self):
        worker = fixtures.SourceScheduler()
        for i in range(40):
            fixtures.record_history(fixtures.video("fav-"+str(i), mid=i+1, source="favorite", fav_time=time.time()))
        self.fixture.save([fixtures.video(str(i), mid=i+1, _detail_complete=False) for i in range(40)])
        with patch.object(worker, "start"):
            worker.request_refill("for_you", "all", 12, fixtures.settings(), "old")
        self.assertEqual(fixtures.rs.recommendation.ready_buffer("for_you", "all", 12, fixtures.settings(), "old")[0], 0)
        with patch.object(fixtures.bili, "_detail_cache", OrderedDict()), patch.object(fixtures.bili, "detail", side_effect=lambda bvid: fixtures.video(bvid, mid=int(bvid)+1)) as detail:
            worker._refill()
        self.assertEqual(detail.call_count, 12)
        self.assertEqual(fixtures.rs.recommendation.ready_buffer("for_you", "all", 12, fixtures.settings(), "old")[0], 1)

    def test_background_high_water_and_demand_bound(self):
        worker = fixtures.SourceScheduler()
        for i in range(40):
            fixtures.record_history(fixtures.video("fav-"+str(i), mid=i+1, source="favorite", fav_time=time.time()))
        self.fixture.save([fixtures.video(str(i), mid=i+1) for i in range(40)])
        with patch.object(worker, "start"):
            worker.request_refill("for_you", "all", 12, fixtures.settings(), "old")
        with patch.object(fixtures.pool, "expand") as expand, patch.object(fixtures.pool, "complete") as complete, patch.object(fixtures.pool, "is_stale", return_value=False):
            worker._refill()
        expand.assert_not_called()
        complete.assert_not_called()
        with patch.object(worker, "start"):
            for i in range(12):
                worker.request_refill("for_you", str(i), 12, fixtures.settings(), "old")
        self.assertEqual(len(worker._demands), 8)
        self.assertTrue(worker._wake.is_set())

    def test_same_page_concurrent_generates_and_records_once(self):
        self.fixture.save([fixtures.video("one")])
        results, errors = [], []
        started, release = threading.Event(), threading.Event()
        original = fixtures.rs.recommendation._generate
        def slow(*args):
            started.set()
            if not release.wait(timeout=3):
                raise RuntimeError("test timeout")
            return original(*args)
        def run():
            try:
                results.append(fixtures.rs.recommendation.refresh("for_you", view_id="concurrent"))
            except Exception as error:
                errors.append(error)
        with patch.object(fixtures.rs.recommendation, "_generate", side_effect=slow) as generate:
            first = threading.Thread(target=run)
            first.start()
            self.assertTrue(started.wait(timeout=2))
            second = threading.Thread(target=run)
            second.start()
            release.set()
            first.join(timeout=5)
            second.join(timeout=5)
        self.assertEqual(errors, [])
        self.assertFalse(first.is_alive() or second.is_alive())
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(results[0], results[1])
        with fixtures.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed_streams").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed_cache").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0], 1)
        self.assertEqual(fixtures.rs.recommendation._stream_locks, {})

    def test_generation_failure_releases_stream_and_retry_reuses_context(self):
        self.fixture.save([fixtures.video("one")])
        with patch.object(fixtures.rs.recommendation, "_record", side_effect=RuntimeError("commit failure")):
            with self.assertRaises(RuntimeError):
                fixtures.rs.recommendation.refresh("for_you", view_id="retry")
        self.assertEqual(fixtures.rs.recommendation._stream_locks, {})
        result = fixtures.rs.recommendation.refresh("for_you", view_id="retry")
        self.assertEqual(len(result["items"]), 1)
        with fixtures.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed_streams").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM recommendation_history").fetchone()[0], 1)

    def test_cache_write_failure_rolls_back_history_and_served(self):
        self.fixture.save([fixtures.video("one")])
        original = fixtures.rs.connect
        class Connection:
            def __init__(self, conn):
                self.conn = conn
            def execute(self, sql, parameters=()):
                if sql.startswith("INSERT INTO feed_cache"):
                    raise sqlite3.OperationalError("injected cache write failure")
                return self.conn.execute(sql, parameters)
        @contextmanager
        def failing():
            with original() as conn:
                yield Connection(conn)
        with patch.object(fixtures.rs, "connect", failing):
            with self.assertRaises(sqlite3.OperationalError):
                fixtures.rs.recommendation.refresh("for_you", view_id="rollback")
        with fixtures.db.connect() as conn:
            for table in ("served_videos", "recommendation_history", "feed_cache"):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)
        self.assertEqual(fixtures.rs.recommendation._stream_locks, {})
        result = fixtures.rs.recommendation.refresh("for_you", view_id="rollback")
        self.assertEqual(len(result["items"]), 1)
        with fixtures.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed_streams").fetchone()[0], 1)

    def test_logout_during_following_head_does_not_write_candidates(self):
        from backend.services.source_scheduler import scheduler
        stopped = threading.Event()
        def head():
            stopped.set()
            return {"items": [fixtures.video("new", source="follow")]}
        with patch.object(scheduler, "_stop", stopped), patch.object(fixtures.bili, "follow_feed", side_effect=head):
            with self.assertRaises(fixtures.rs.FeedError):
                fixtures.rs.recommendation.refresh("following", view_id="logout")
        self.assertEqual(fixtures.pool.all(("follow",)), [])
        with fixtures.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM feed_cache").fetchone()[0], 0)

    def test_successful_head_consumes_recall_budget_and_is_saved(self):
        def head():
            with fixtures.rs.requests_scope.managed("https://api.bilibili.com/head"):
                fixtures.rs.requests_scope.consume(fixtures.rs.requests_scope.current())
            return {"items": [fixtures.video("new", source="follow")]}
        with patch.object(fixtures.bili, "follow_feed", side_effect=head):
            result = fixtures.rs.recommendation.refresh("following", view_id="head-budget")
        self.assertEqual(result["following_freshness"]["status"], "checked")
        self.assertEqual([v["bvid"] for v in result["items"]], ["new"])

    def test_cross_stream_commit_rechecks_cooldown(self):
        for i in range(30):
            fixtures.record_history(fixtures.video("fav-"+str(i), mid=i+1, source="favorite", fav_time=time.time()))
            # related 连续两次未点击才冷却，第一批提交触发第二次。
            self.fixture.position(str(i), served_at=time.time()-1801)
        self.fixture.save([fixtures.video(str(i), mid=i+1) for i in range(30)])
        barrier = threading.Barrier(2)
        original = fixtures.rs.recommendation.rank_sources
        results, errors = [], []
        def rank(*args, **kwargs):
            value = original(*args, **kwargs)
            if kwargs.get("record_hits") is False:
                barrier.wait(timeout=5)
            return value
        def run(view):
            try:
                results.append(fixtures.rs.recommendation.refresh("for_you", view_id=view))
            except Exception as error:
                errors.append(str(error))
        with patch.object(fixtures.rs.recommendation, "rank_sources", side_effect=rank):
            workers = [threading.Thread(target=run, args=(str(i),)) for i in range(2)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(timeout=10)
        self.assertEqual(errors, [])
        self.assertTrue(all(not worker.is_alive() for worker in workers))
        self.assertEqual([len(page["items"]) for page in results], [12, 12])
        self.assertFalse({v["bvid"] for v in results[0]["items"]} & {v["bvid"] for v in results[1]["items"]})

    def test_following_legacy_string_dates_use_latest_prefix(self):
        self.fixture.save([fixtures.video("older", source="follow", pubdate=str(time.time()-100)),
                           fixtures.video("newer", source="follow", pubdate=time.time()-10),
                           fixtures.video("invalid", source="follow", pubdate="")])
        result = fixtures.rs.recommendation.refresh("following", view_id="legacy-dates")
        self.assertEqual(result["following_freshness"]["status"], "checked")
        self.assertEqual([v["bvid"] for v in result["items"]], ["newer", "older", "invalid"])
