"""使用真实 requests/urllib3 和本机 HTTP 验证隐式发送，不访问外网。"""
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from backend.services.bilibili_service import BilibiliService
from backend.services import request_coordination as coordination
from backend.services import feed_performance as perf


class RequestCoordinationTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        calls = self.calls
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                path = self.path.split("?")[0]
                calls.append((path, time.monotonic()))
                status = 302 if path == "/redirect" else 500 if path in ("/retry", "/retry-after") and sum(p == path for p, _ in calls) == 1 else 200
                self.send_response(status)
                if status == 302:
                    self.send_header("Location", "/done")
                if path == "/retry-after" and status == 500:
                    self.send_header("Retry-After", "20")
                body = json.dumps({"code": 0, "data": {"wbi_img": {"img_url": "https://example.test/" + "a"*32 + ".png", "sub_url": "https://example.test/" + "b"*32 + ".png"}} if path == "/nav" else {"ok": True}}).encode()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.url = "http://127.0.0.1:" + str(self.server.server_port)
        self.service = BilibiliService()
        self.service._cookie = ""
        self.slots = []
        original = self.service._before_send
        def gate(*args, **kwargs):
            original(*args, **kwargs)
            self.slots.append(time.perf_counter())
        for adapter in set(self.service._session.adapters.values()):
            adapter.gate = gate
            adapter.max_retries.gate = gate
        self.interval = patch("backend.services.bilibili_service.SOURCE_REQUEST_INTERVAL", .04)
        self.interval.start()

    def tearDown(self):
        self.service._session.close()
        self.server.shutdown()
        self.server.server_close()
        self.worker.join(timeout=2)
        self.interval.stop()

    def test_retry_and_redirect_share_real_slots(self):
        records = []
        with perf.trace(enabled=True, sink=records), coordination.scope(total=4, timeout=2):
            self.service.get(self.url + "/retry")
            self.service.get(self.url + "/redirect")
        self.assertEqual([p for p, _ in self.calls], ["/retry", "/retry", "/redirect", "/done"])
        self.assertEqual(records[0]["http_attempts"], 4)
        self.assertTrue(all(b-a >= .038 for a, b in zip(self.slots, self.slots[1:])), self.slots)

    def test_failed_retry_consumes_attempt_budget(self):
        with coordination.scope(total=1), self.assertRaises(coordination.BudgetEnded):
            self.service.get(self.url + "/retry")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(coordination.current(), {})

    def test_retry_after_is_cancelled_by_soft_deadline(self):
        start = time.monotonic()
        with coordination.scope(total=3, timeout=.05), self.assertRaises(coordination.BudgetEnded):
            self.service.get(self.url + "/retry-after")
        self.assertEqual(len(self.calls), 1)
        self.assertLess(time.monotonic() - start, .5)

    def test_cold_signing_nav_does_not_share_business_slot(self):
        with patch.object(self.service, "nav", side_effect=lambda force=False: self.service.get(self.url + "/nav")), coordination.scope(total=2):
            self.service.get(self.url + "/business", wbi=True)
        self.assertEqual([p for p, _ in self.calls], ["/nav", "/business"])
        self.assertGreaterEqual(self.calls[1][1]-self.calls[0][1], .035)

    def test_stopped_scope_has_no_http_and_perf_is_optional(self):
        stop = threading.Event()
        stop.set()
        with coordination.scope(stop=stop), self.assertRaises(coordination.BudgetEnded):
            self.service.get(self.url + "/done")
        self.assertEqual(self.calls, [])
        with coordination.scope(total=1):
            self.service.get(self.url + "/done")
        self.assertEqual(len(self.calls), 1)

    def test_foreground_next_slot_fifo_and_background_fairness(self):
        order, errors, workers = [], [], []
        self.service._last_request_at = time.perf_counter() + .5
        def run(label):
            try:
                with coordination.scope(background=label.startswith("B"), total=1):
                    self.service._before_send(self.url)
                    order.append(label)
            except Exception as error:
                errors.append(error)
        for label in ("B0", "B1", "F0", "F1", "F2", "F3", "F4", "F5"):
            worker = threading.Thread(target=run, args=(label,))
            workers.append(worker)
            worker.start()
            deadline = time.perf_counter()+.2
            while len(self.service._waiting) < len(workers) and time.perf_counter() < deadline:
                time.sleep(.001)
        for worker in workers:
            worker.join(timeout=3)
        self.assertEqual(errors, [])
        self.assertTrue(all(not v.is_alive() for v in workers))
        self.assertEqual(order, ["F0", "F1", "F2", "B0", "F3", "F4", "F5", "B1"])
        self.assertEqual(self.service._waiting, [])

    def test_waiting_stop_and_rate_limit_clear_tickets(self):
        self.service._cookie = "fixture"
        self.service._last_request_at = time.perf_counter()+2
        for reason in ("stop", "rate_limited"):
            stop, errors = threading.Event(), []
            def run():
                try:
                    with coordination.scope(stop=stop, candidate=True, total=1):
                        self.service._before_send(self.url)
                except coordination.BudgetEnded as error:
                    errors.append(str(error))
            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.perf_counter()+1
            while not self.service._waiting and time.perf_counter() < deadline:
                time.sleep(.001)
            if reason == "stop":
                stop.set()
            else:
                self.service.rate_limited_until = time.time()+10
            self.service.wake_requests()
            worker.join(timeout=2)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, ["stopped" if reason == "stop" else "rate_limited"])
            self.assertEqual(self.service._waiting, [])
        self.assertEqual(self.calls, [])
