"""第一轮诊断采样；默认假 HTTP 回放，--real 只在生产数据库副本上低频采样。"""
import argparse
from collections import OrderedDict
from contextlib import ExitStack, closing
import csv
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from backend.config import DB_PATH
from backend.services.bilibili_service import bili
from backend.services import feed_performance as perf
from backend.services import request_coordination as requests_scope
from backend.services import recommendation_service as rs
from backend.services.cache_service import pool
from backend.services import filter_service as fs
from backend.storage import database as db
from backend.services.model_registry import registry


def summarize(records, output):
    output.mkdir(parents=True, exist_ok=True)
    (output / "traces.jsonl").write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    keys = ["scenario", "total_ms", "bilibili_api_calls", "detail_requests", "http_attempts", "detail_http_attempts", "rank_ms", "mixer_ms", "gen_lock_wait_ms", "stream_lock_wait_ms", "request_queue_wait_ms",
            "rate_limiter_lock_wait_ms", "rate_limiter_sleep_ms", "feed_page_cache_hit", "returned_count"]
    with (output / "summary.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=keys)
        writer.writeheader()
        for row in records:
            writer.writerow({key: row.get(key) for key in keys})
    for scenario in sorted({row.get("scenario", "unknown") for row in records}):
        rows = [row for row in records if row.get("scenario", "unknown") == scenario]
        values = sorted(row["total_ms"] for row in rows)
        print(json.dumps({"scenario": scenario, "n": len(values), "p50_ms": statistics.median(values),
                          "p95_ms": values[math.ceil(len(values)*.95)-1], "max_ms": max(values),
                          "api_mean": sum(row["bilibili_api_calls"] for row in rows)/len(rows),
                          "detail_mean": sum(row["detail_requests"] for row in rows)/len(rows)}, ensure_ascii=False), flush=True)


class ReplaySession:
    def get(self, url, headers=None, params=None, timeout=None):
        # 假传输也经过同一个准入点，回放不能绕过预算和调度。
        bili._before_send(url)
        time.sleep(.002)
        params = params or {}
        if url.endswith("/archive/related"):
            data = [{"bvid": f"new-{params['bvid']}-{i}", "title": "回放", "owner": {"mid": 2000+i, "name": "回放UP"},
                     "duration": 100, "pubdate": int(time.time()), "stat": {"view": 10000}} for i in range(20)]
        elif url.endswith("/view/detail"):
            data = {"View": {"bvid": params["bvid"], "title": "回放", "owner": {"mid": 2000, "name": "回放UP"},
                             "stat": {"view": 10000, "like": 100, "coin": 100, "favorite": 100, "share": 0, "reply": 0},
                             "duration": 100, "pubdate": int(time.time()), "tname": "科技"}, "Tags": [{"tag_name": "测试"}]}
        else:
            data = {}
        return SimpleNamespace(status_code=200, json=lambda: {"code": 0, "data": data},
                               raw=SimpleNamespace(retries=SimpleNamespace(history=[])))


def replay(output, repeats):
    from backend.tests import test_v031 as fixtures
    from backend.services.training_data import record_history
    records = []
    fixture = fixtures.V031Test("test_default_quota_and_native_share")
    fixture.setUp()
    try:
        for item in fixture.patches:
            if item.attribute == "expand":
                item.stop()
        for source, videos in fixture.pools(40).items():
            fixture.save(videos)
            for video in videos:
                if source == "follow":
                    fixture.following(video["mid"])
                if source == "up_archive":
                    record_history({**video, "source": "favorite", "isfaved": 1})
        baseline = sqlite3.connect(":memory:")
        with closing(sqlite3.connect(db.DB_PATH)) as conn:
            conn.backup(baseline)
        def restore():
            with closing(sqlite3.connect(db.DB_PATH)) as conn:
                baseline.backup(conn)
            fs.filters.invalidate()
            bili._detail_cache.clear()
            bili._last_request_at = 0
        def call(scenario, view=None, enabled=True):
            with perf.trace(enabled=enabled, sink=records, scenario=scenario):
                return rs.recommendation.refresh("for_you", view_id=view or uuid.uuid4().hex)
        with patch.object(bili, "_session", ReplaySession()), patch.object(bili, "_detail_cache", OrderedDict()), \
                patch("backend.services.bilibili_service.SOURCE_REQUEST_INTERVAL", .006):
            for _ in range(repeats):
                restore()
                call("warm-refresh")
                call("second-refresh", view="cache")
                call("cached-page-repeat", view="cache")
                restore()
                with db.connect() as conn:
                    conn.execute("UPDATE candidates SET data=json_set(data,'$._detail_complete',0)")
                call("detail-cold")
            restore()
            start = time.perf_counter()
            call("off-control", enabled=False)
            print(json.dumps({"diagnostics_off_control_ms": (time.perf_counter()-start)*1000}), flush=True)
            # 已生成的缓存页与另一新批次同时请求，制造实际 Feed 生成锁竞争。
            restore()
            call("cache-prime", view="prime")
            entered = threading.Event()
            original = rs.recommendation._generate
            def signal(*args):
                entered.set()
                return original(*args)
            with patch.object(rs.recommendation, "_generate", side_effect=signal):
                worker = threading.Thread(target=lambda: call("concurrent-generating"))
                worker.start()
                entered.wait(timeout=5)
                call("concurrent-cached", view="prime")
                worker.join(timeout=15)
            # 固定缓存下的后台节流竞争，只发假 HTTP，不变更候选内容。
            restore()
            started = threading.Event()
            def background():
                with requests_scope.scope(background=True), perf.trace("source_scheduler", enabled=True, sink=records, scenario="background-contender"):
                    started.set()
                    for _ in range(20):
                        bili.get("https://api.bilibili.com/replay")
            worker = threading.Thread(target=background)
            worker.start()
            started.wait(timeout=5)
            call("background-competition")
            worker.join(timeout=15)
        baseline.close()
    finally:
        fixture.tearDown()
    summarize(records, output)


def real(output, count):
    if not bili.has_cookie:
        raise RuntimeError("没有现有登录态，停止真实采样")
    records = []
    state = registry.state()
    with tempfile.TemporaryDirectory(prefix="feed-perf-") as root, ExitStack() as patches:
        target = str(Path(root) / "snapshot.db")
        with closing(sqlite3.connect(Path(DB_PATH).as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        with closing(sqlite3.connect(target)) as migration:
            migration.row_factory = sqlite3.Row
            from backend.storage.migrate_v032 import upgrade
            upgrade(migration)
            from backend.storage.migrate_v031 import migrate
            migrate(migration)
            migration.commit()
        patches.enter_context(patch.object(db, "DB_PATH", target))
        patches.enter_context(patch.object(fs, "DB_PATH", target))
        patches.enter_context(patch.object(rs.recommendation, "prefetch"))
        patches.enter_context(patch.object(registry, "state", return_value={**state, "trial": None}))
        from backend.services.source_mixer import update_settings
        update_settings({"hot": "fallback", "rcmd": "small", "classic": False})
        fs.filters.invalidate()
        registry.load(state.get("active") or "fallback-v03")
        if count == 0:
            # 因果对照：只在临时副本中禁止同步外部补充，验证既有完整候选能否成页。
            with patch.object(pool, "expand", return_value=0), patch.object(pool, "complete", return_value=0):
                with perf.trace(enabled=True, sink=records, scenario="real-snapshot-cache-only"):
                    result = rs.recommendation.refresh("for_you", view_id=uuid.uuid4().hex)
                print(json.dumps({"total_ms": records[-1]["total_ms"], "returned": len(result["items"])}, ensure_ascii=False), flush=True)
        for index in range(count):
            view = uuid.uuid4().hex
            before = {source: {"raw": len(pool.all((source,))), "complete": sum(bool(v.get("_detail_complete")) for v in pool.all((source,)))} for source in pool._locks}
            with perf.trace(enabled=True, sink=records, scenario="real-warm-refresh", pool_before=before):
                result = rs.recommendation.refresh("for_you", view_id=view)
            print(json.dumps({"sample": index+1, "total_ms": records[-1]["total_ms"], "api": records[-1]["bilibili_api_calls"],
                              "detail": records[-1]["detail_requests"], "returned": len(result["items"])}, ensure_ascii=False), flush=True)
            with perf.trace(enabled=True, sink=records, scenario="real-cached-page"):
                rs.recommendation.refresh("for_you", view_id=view)
            if bili.rate_limited:
                break
            time.sleep(2)
        summarize(records, output)


def benchmark(output, repeats, policy=False):
    """同快照 HTTP 热缓存验收；临时副本恢复在计时外，禁止外部请求。"""
    from backend.app import create_app
    from backend.services.source_mixer import update_settings
    records = []
    state = registry.state()
    with tempfile.TemporaryDirectory(prefix="feed-benchmark-") as root, ExitStack() as patches:
        target = str(Path(root) / "snapshot.db")
        with closing(sqlite3.connect(Path(DB_PATH).as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(target)) as dest:
            source.backup(dest)
        with closing(sqlite3.connect(target)) as migration:
            migration.row_factory = sqlite3.Row
            from backend.storage.migrate_v032 import upgrade
            upgrade(migration)
            from backend.storage.migrate_v031 import migrate
            migrate(migration)
            migration.commit()
        patches.enter_context(patch.object(db, "DB_PATH", target))
        patches.enter_context(patch.object(fs, "DB_PATH", target))
        patches.enter_context(patch.object(rs.recommendation, "prefetch"))
        patches.enter_context(patch.object(registry, "state", return_value={**state, "trial": None}))
        patches.enter_context(patch.object(bili, "get", side_effect=AssertionError("热缓存发生外部请求")))
        patches.enter_context(patch.dict(os.environ, {"BILIPERSONAL_FEED_PERF": "1", "BILIPERSONAL_FEED_PERF_DEEP": "0"}))
        update_settings({"hot": "fallback", "rcmd": "small", "classic": False})
        fs.filters.invalidate()
        registry.load(state.get("active") or "fallback-v03")
        if policy:
            from backend.services.interest_profile import publish
            from backend.services.recommendation_policy import signature, settings as policy_settings
            publish()
            db.set_state("policy:trial", {"status": "running", "signature": signature(policy_settings()), "model_version": state.get("active") or "fallback-v03", "started_at": time.time()})
        app = create_app(start_background=False)
        client = app.test_client()
        baseline = sqlite3.connect(":memory:")
        with closing(sqlite3.connect(target)) as conn:
            conn.backup(baseline)
        original_trace = perf.trace
        scenario = "http-warm-new"
        def trace(*args, **kwargs):
            kwargs.setdefault("sink", records)
            kwargs.setdefault("scenario", scenario)
            return original_trace(*args, **kwargs)
        def request(method, url, **kwargs):
            response = getattr(client, method)(url, **kwargs)
            if response.status_code != 200:
                raise RuntimeError(f"HTTP {response.status_code}: {response.get_json()}")
            return response.get_json()
        with patch.object(perf, "trace", side_effect=trace):
            for _ in range(repeats):
                with closing(sqlite3.connect(target)) as conn:
                    baseline.backup(conn)
                fs.filters.invalidate()
                scenario = "http-warm-new"
                page = request("post", "/api/v1/feed/refresh", json={"view_id": uuid.uuid4().hex})
                scenario = "http-cached-page"
                request("get", f"/api/v1/feed?cursor={page['stream_id']}:0")
                scenario = "http-cached-refresh"
                request("post", "/api/v1/feed/refresh", json={"view_id": page["view_id"]})
            for same_stream in (False, True):
                entered, release = threading.Event(), threading.Event()
                errors = []
                original_generate = rs.recommendation._generate
                def slow(*args):
                    entered.set()
                    if not release.wait(timeout=15):
                        raise RuntimeError("竞争测试未释放生成请求")
                    return original_generate(*args)
                def contender():
                    try:
                        with original_trace(enabled=True, sink=records, scenario="controlled-generation"):
                            if same_stream:
                                rs.recommendation.get_feed("for_you", cursor=f"{page['stream_id']}:1")
                            else:
                                rs.recommendation.refresh("for_you", view_id=uuid.uuid4().hex)
                    except Exception as error:
                        errors.append(str(error))
                with patch.object(rs.recommendation, "_generate", side_effect=slow):
                    worker = threading.Thread(target=contender)
                    worker.start()
                    try:
                        if not entered.wait(timeout=5):
                            raise RuntimeError("竞争请求未进入生成阶段")
                        scenario = "http-cache-same-stream-contention" if same_stream else "http-cache-cross-stream-contention"
                        for _ in range(repeats):
                            request("get", f"/api/v1/feed?cursor={page['stream_id']}:0")
                    finally:
                        release.set()
                        worker.join(timeout=10)
                    if worker.is_alive() or errors:
                        raise RuntimeError(f"竞争请求失败: {errors}")
        baseline.close()
    summarize(records, output)


def lock_probe(output):
    """只模拟后台占用共享节流槽，测前台真实 acquire 等待，不请求外网。"""
    records, entered = [], threading.Event()
    with patch.object(bili, "_session", ReplaySession()), patch.object(bili, "_last_request_at", 0):
        def background():
            with perf.trace("source_scheduler", enabled=True, sink=records, scenario="controlled-background-slot"):
                with perf.lock(bili._lock, "rate_limiter_lock"):
                    entered.set()
                    time.sleep(.08)
                    # 只制造锁竞争，不另加一秒主动 sleep，保留真实 get() 分支。
        worker = threading.Thread(target=background)
        worker.start()
        if not entered.wait(timeout=5):
            raise RuntimeError("受控后台未启动")
        with perf.trace(enabled=True, sink=records, scenario="controlled-foreground-wait"):
            bili.get("https://api.bilibili.com/replay")
        worker.join(timeout=5)
    summarize(records, output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", action="store_true", help="仅在性能副本启用新策略")
    parser.add_argument("--real", action="store_true")
    parser.add_argument("--cache-only", action="store_true", help="仅在真实数据库副本上验证缓存成页，不发外部请求")
    parser.add_argument("--lock-probe", action="store_true", help="使用假 HTTP 验证后台节流槽与前台竞争")
    parser.add_argument("--benchmark", action="store_true", help="同快照 HTTP 热缓存与缓存竞争验收，零外部请求")
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--repeats", type=int, default=10)
    parser.add_argument("--output", type=Path, default=Path(".tmp/feed-performance/replay"))
    args = parser.parse_args()
    if args.count < 1 or args.count > 10 or args.repeats < 1:
        parser.error("真实样本数限 1～10；回放重复次数至少 1")
    if args.benchmark:
        if args.repeats < 50:
            parser.error("性能验收每组样本至少 50")
        benchmark(args.output, args.repeats, args.policy)
    elif args.lock_probe:
        lock_probe(args.output)
    elif args.real or args.cache_only:
        real(args.output, 0 if args.cache_only else args.count)
    else:
        replay(args.output, args.repeats)
