"""第一层诊断回归；假 HTTP、独立数据库，不请求 B 站。"""
import json
import os
import threading
import time
import unittest
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import patch

from backend.tests import test_v031 as fixtures
from backend.services import feed_performance as perf
from backend.services.bilibili_service import bili, BiliError
from backend.services.source_scheduler import SourceScheduler
from backend.storage import database as db


def response(bvid="BV-test", retries=0):
    return SimpleNamespace(status_code=200, raw=SimpleNamespace(retries=SimpleNamespace(history=[None] * retries)),
                           json=lambda: {"code": 0, "data": {"View": {"bvid": bvid, "owner": {"mid": 1, "name": "测试"},
                               "stat": {"view": 10000, "like": 100, "favorite": 100, "coin": 100, "share": 0, "reply": 0}}}})


class FeedPerformanceTest(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.V031Test("test_default_quota_and_native_share")
        self.fixture.setUp()
        self.patches = [patch.dict(os.environ, {"BILIPERSONAL_FEED_PERF_DEEP": "0"}),
                        patch.object(bili, "_detail_cache", OrderedDict()), patch.object(bili, "_last_request_at", 0),
                        patch("backend.services.bilibili_service.SOURCE_REQUEST_INTERVAL", 0)]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.fixture.tearDown()

    def test_http_detail_cache_and_retry_counts(self):
        records = []
        with patch.object(bili._session, "get", return_value=response(retries=2)) as http, perf.trace(enabled=True, sink=records):
            bili.detail("BV-test")
            bili.detail("BV-test")
        self.assertEqual(http.call_count, 1)
        self.assertEqual(records[0]["detail_calls"], 2)
        self.assertEqual(records[0]["detail_requests"], 1)
        self.assertEqual(records[0]["detail_cache_hits"], 1)
        self.assertEqual(records[0]["http_retries"], 2)
        self.assertEqual(records[0]["api"][0]["attempts"], 3)

    def test_exception_is_recorded_and_context_cleared(self):
        records = []
        with patch.object(bili._session, "get", return_value=SimpleNamespace(status_code=500)), self.assertRaises(BiliError):
            with perf.trace(enabled=True, sink=records):
                bili.get("https://api.bilibili.com/example", {"secret": "do-not-log"})
        self.assertEqual(records[0]["bilibili_api_calls"], 1)
        self.assertEqual(records[0]["error_type"], "BiliError")
        self.assertNotIn("do-not-log", json.dumps(records))
        self.assertIsNone(perf.current())

    def test_concurrent_counts_do_not_include_background(self):
        records = []
        def run(owner, count):
            with perf.trace(owner, enabled=True, sink=records):
                for _ in range(count):
                    bili.get("https://api.bilibili.com/example")
        with patch.object(bili._session, "get", return_value=response()):
            tasks = [threading.Thread(target=run, args=("foreground_feed", 2)), threading.Thread(target=run, args=("source_scheduler", 3))]
            for task in tasks:
                task.start()
            for task in tasks:
                task.join(timeout=5)
        self.assertEqual({v["owner"]: v["bilibili_api_calls"] for v in records}, {"foreground_feed": 2, "source_scheduler": 3})

    def test_nonblocking_scheduler_busy_is_not_wait(self):
        records, worker = [], SourceScheduler()
        worker._lock.acquire()
        try:
            with perf.trace(enabled=True, sink=records):
                worker.tick()
        finally:
            worker._lock.release()
        self.assertEqual(records[0]["source_scheduler_lock_busy_count"], 1)
        self.assertFalse(records[0]["waited_source_scheduler_lock"])

    def test_limiter_sleep_and_busy_wait_are_separate(self):
        records = []
        bili._lock.acquire()
        release = threading.Thread(target=lambda: (time.sleep(.04), bili._lock.release()))
        release.start()
        bili._last_request_at = time.perf_counter()
        with patch("backend.services.bilibili_service.SOURCE_REQUEST_INTERVAL", .08), patch.object(bili._session, "get", return_value=response()):
            with perf.trace(enabled=True, sink=records):
                bili._before_send("https://api.bilibili.com/example")
                bili.get("https://api.bilibili.com/example")
        release.join()
        self.assertTrue(records[0]["waited_rate_limiter"])
        self.assertGreater(records[0]["rate_limiter_lock_wait_ms"], 20)
        self.assertGreater(records[0]["rate_limiter_sleep_ms"], 10)

    def test_cache_page_branch_and_deep_cache_ratio(self):
        records = []
        self.fixture.save([fixtures.video(str(i), mid=i) for i in range(1, 6)])
        with patch.dict(os.environ, {"BILIPERSONAL_FEED_PERF_DEEP": "1"}):
            with perf.trace(enabled=True, sink=records):
                first = fixtures.rs.recommendation.refresh("for_you", view_id="same-view")
            with perf.trace(enabled=True, sink=records):
                second = fixtures.rs.recommendation.refresh("for_you", view_id="same-view")
        self.assertEqual(first, second)
        self.assertEqual(records[0]["candidate_cache_hit_rate"], 1)
        self.assertFalse(records[0]["feed_page_cache_hit"])
        self.assertTrue(records[1]["feed_page_cache_hit"])
        self.assertIsNone(records[1]["candidate_cache_hit_rate"])
        self.assertEqual(records[1]["bilibili_api_calls"], 0)

    def test_sql_deep_excludes_business_sleep_and_scrubs_literals(self):
        records = []
        with patch.dict(os.environ, {"BILIPERSONAL_FEED_PERF_DEEP": "1"}), perf.trace(enabled=True, sink=records):
            with db.connect() as conn:
                self.assertEqual(conn.execute("SELECT 'private-value'").fetchone()[0], "private-value")
                time.sleep(.03)
        self.assertGreater(records[0]["total_ms"], 25)
        self.assertLess(records[0]["SQLite_ms"], records[0]["total_ms"] - 20)
        self.assertNotIn("private-value", json.dumps(records))

    def test_basic_does_not_enable_deep_sql(self):
        records = []
        with perf.trace(enabled=True, sink=records):
            with db.connect() as conn:
                conn.execute("SELECT 1").fetchall()
        self.assertIsNone(records[0]["SQLite_ms"])
        self.assertEqual(records[0]["sql"], [])

    def test_cached_feed_does_not_wait_for_other_generation(self):
        records = []
        self.fixture.save([fixtures.video(str(i), mid=i) for i in range(1, 6)])
        fixtures.rs.recommendation.refresh("for_you", view_id="cached")
        entered, finish = threading.Event(), threading.Event()
        original = fixtures.rs.recommendation._generate
        def slow(*args):
            entered.set()
            finish.wait(timeout=2)
            return original(*args)
        def generate():
            with perf.trace(enabled=True, sink=records):
                fixtures.rs.recommendation.refresh("for_you", view_id="slow")
        with patch.object(fixtures.rs.recommendation, "_generate", side_effect=slow):
            worker = threading.Thread(target=generate)
            worker.start()
            self.assertTrue(entered.wait(timeout=2))
            release = threading.Thread(target=lambda: (time.sleep(.05), finish.set()))
            release.start()
            with perf.trace(enabled=True, sink=records):
                fixtures.rs.recommendation.refresh("for_you", view_id="cached")
            worker.join(timeout=5)
            release.join()
        cached = next(v for v in records if v["feed_page_cache_hit"])
        self.assertLess(cached["gen_lock_wait_ms"], 10)
        self.assertFalse(worker.is_alive())
        self.assertTrue(all("error_type" not in v for v in records))
        self.assertEqual(cached["bilibili_api_calls"], 0)

    def test_http_trace_header_and_invalid_request(self):
        from backend.app import create_app
        records = []
        app = create_app(start_background=False)
        with patch.dict(os.environ, {"BILIPERSONAL_FEED_PERF": "1"}), patch.object(perf, "trace", wraps=perf.trace) as traced:
            # 用同一上下文管理器输出到测试 sink，避免生成真实诊断文件。
            traced.side_effect = lambda *args, **kwargs: perf_context(*args, sink=records, **kwargs)
            with app.test_client() as client:
                result = client.get("/api/v1/feed?type=invalid")
        self.assertEqual(result.status_code, 400)
        self.assertEqual(result.headers["X-Feed-Trace-Id"], records[0]["trace_id"])
        self.assertIsNone(perf.current())


perf_context = perf.trace
