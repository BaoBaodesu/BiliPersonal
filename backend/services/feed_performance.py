"""可关闭的 Feed 诊断：请求独立计数，后台不混入前台，不记录凭证或 SQL 参数。"""
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import json
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

SOURCES = ("follow", "up_archive", "related", "rcmd", "hot")
_current = ContextVar("feed_performance", default=None)
_write_lock = threading.Lock()
_metrics = ("rank_ms", "rank_prepare_ms", "mixer_ms", "SQLite_ms", "model_load_ms", "affinity_ms",
            "filter_ms", "cooldown_ms", "record_ms", "response_json_ms", "json_decode_ms",
            "gen_lock_wait_ms", "stream_lock_wait_ms", "request_queue_wait_ms", "rate_limiter_lock_wait_ms", "rate_limiter_sleep_ms", "scheduler_start_lock_wait_ms")
_counts = ("http_attempts", "detail_http_attempts", "bilibili_api_calls", "detail_requests", "detail_calls", "detail_cache_hits", "sync_candidates_added",
           "sync_detail_completed", "detail_attempted", "detail_failed", "pool_read_calls", "pool_rows_read",
           "sqlite_connect_count", "sqlite_statement_count", "source_pool_lock_busy_count", "source_scheduler_lock_busy_count",
           "backoff_skipped_count")


def current():
    return _current.get()


class Trace:
    def __init__(self, owner, **info):
        self.started = time.perf_counter_ns()
        self.deep = os.environ.get("BILIPERSONAL_FEED_PERF_DEEP") == "1"
        self.info = {"trace_id": uuid.uuid4().hex, "owner": owner, **info}
        self.values = Counter({key: 0 for key in (*_metrics, *_counts)})
        self.sources = {s: {"status": "not_used", "source_ms": 0, "metrics": Counter()} for s in SOURCES}
        self.spans, self.stack, self.api, self.sql = [], [], [], []
        self.source = None
        self.cached = set()
        self.baseline_frozen = False
        self.info.update(waited_rate_limiter=False, waited_source_scheduler_lock=False,
                         feed_page_cache_hit=False, candidate_cache_hit_rate=None)

    def result(self):
        def union(intervals):
            end, total = 0, 0
            for start, stop in sorted(intervals):
                total += max(0, stop - max(start, end))
                end = max(end, stop)
            return total
        # 来源阶段是重叠视角；关键路径按叶级区间扣除子区间，不能累加所有指标。
        phases = {"pool_read_ms", "recall_ms", "detail_complete_ms", "rank_ms"}
        for source, value in self.sources.items():
            value["source_ms"] = union([(s["start_ms"], s["end_ms"]) for s in self.spans if s["source"] == source and s["metric"] in phases])
        exclusive, children = Counter(), {}
        for s in self.spans:
            children.setdefault(s["parent"], []).append((s["start_ms"], s["end_ms"]))
        for index, s in enumerate(self.spans):
            exclusive[s["metric"]] += max(0, s["end_ms"] - s["start_ms"] - union(children.get(index, [])))
        total = (time.perf_counter_ns() - self.started) / 1e6
        covered = union([(s["start_ms"], s["end_ms"]) for s in self.spans if s["parent"] is None])
        if not self.deep:
            self.values["SQLite_ms"] = None
            for metric in ("sync_candidates_added", "sync_detail_completed", "sqlite_connect_count", "sqlite_statement_count"):
                self.values[metric] = None
            self.info["diagnostic_level"] = "basic"
        else:
            self.info["diagnostic_level"] = "deep"
        return {**self.info, **self.values, "total_ms": total, "sources": self.sources,
                "spans": self.spans, "api": self.api, "sql": self.sql,
                "exclusive_ms": dict(exclusive), "other_ms": max(0, total - covered)}


@contextmanager
def trace(owner="foreground_feed", enabled=None, sink=None, **info):
    if enabled is None:
        enabled = os.environ.get("BILIPERSONAL_FEED_PERF") == "1"
    if not enabled:
        yield None
        return
    value = Trace(owner, **info)
    token = _current.set(value)
    try:
        yield value
    except Exception as error:
        value.info["error_type"] = type(error).__name__
        raise
    finally:
        result = value.result()
        _current.reset(token)
        if sink is not None:
            sink.append(result)
        else:
            path = Path(os.environ.get("BILIPERSONAL_FEED_PERF_LOG", ".tmp/feed-performance/traces.jsonl"))
            try:
                with _write_lock:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("a", encoding="utf-8") as out:
                        out.write(json.dumps(result, ensure_ascii=False) + "\n")
            except OSError:
                # 诊断写入失败不改变正常 Feed 响应。
                pass


def add(metric, amount=1):
    value = current()
    if value is not None:
        value.values[metric] += amount
        if value.source in value.sources:
            value.sources[value.source]["metrics"][metric] += amount


@contextmanager
def span(metric, source=None):
    value = current()
    if value is None or (metric == "json_decode_ms" and not value.deep):
        yield
        return
    previous = value.source
    if source in SOURCES:
        value.source = source
        value.sources[source]["status"] = "used"
    index = len(value.spans)
    start = time.perf_counter_ns()
    value.spans.append({"metric": metric, "source": value.source, "parent": value.stack[-1] if value.stack else None,
                        "start_ms": (start - value.started) / 1e6})
    value.stack.append(index)
    try:
        yield
    finally:
        end = time.perf_counter_ns()
        value.spans[index]["end_ms"] = (end - value.started) / 1e6
        add(metric, (end - start) / 1e6)
        value.stack.pop()
        value.source = previous


def measured(metric, source_arg=None):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if current() is None:
                return fn(*args, **kwargs)
            source = args[source_arg] if source_arg is not None and len(args) > source_arg else kwargs.get("source")
            if isinstance(source, (tuple, list)):
                source = source[0] if len(source) == 1 else None
            with span(metric, source):
                return fn(*args, **kwargs)
        return wrapper
    return decorate


def background(owner):
    def decorate(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            if current() is not None or os.environ.get("BILIPERSONAL_FEED_PERF") != "1":
                return fn(*args, **kwargs)
            with trace(owner, operation=fn.__name__):
                return fn(*args, **kwargs)
        return wrapper
    return decorate


def decode_json(data):
    if current() is None or not current().deep:
        return json.loads(data)
    with span("json_decode_ms"):
        return json.loads(data)


def json_response(data):
    from flask import jsonify
    with span("response_json_ms"):
        return jsonify(data)


@contextmanager
def lock(resource, name):
    value = current()
    if value is None:
        with resource:
            yield
        return
    busy = resource.locked()
    with span(name + "_wait_ms"):
        resource.acquire()
    if name == "rate_limiter_lock" and busy:
        value.info["waited_rate_limiter"] = True
    add(name + "_busy_count", int(busy))
    try:
        with span(name + "_held_ms"):
            yield
    finally:
        resource.release()


def try_lock(resource, metric):
    acquired = resource.acquire(blocking=False)
    if not acquired:
        add(metric)
    return acquired


def observe_pool(videos):
    value = current()
    if value is not None and value.deep and not value.baseline_frozen:
        value.cached.update((v.get("source"), v["bvid"]) for v in videos if v.get("_detail_complete") is True)


def returned(items):
    value = current()
    if value is None:
        return
    value.info["returned_count"] = len(items)
    for source in SOURCES:
        value.sources[source]["returned"] = sum(v.get("source") == source for v in items)
    if value.info["feed_page_cache_hit"] or not value.deep:
        return
    hits = sum((v.get("source"), v["bvid"]) in value.cached for v in items)
    value.info.update(candidate_cache_hits=hits, candidate_cache_total=len(items),
                      candidate_cache_hit_rate=hits / len(items) if items else None)


class TimedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        return self._run("execute", sql, parameters)

    def executemany(self, sql, parameters):
        return self._run("executemany", sql, parameters)

    def executescript(self, sql):
        return self._run("executescript", sql)

    def _run(self, method, sql, *args):
        add("sqlite_statement_count")
        start = time.perf_counter_ns()
        try:
            with span("SQLite_ms"):
                return getattr(super(), method)(sql, *args)
        finally:
            value = current()
            if value is not None:
                # 仅保留无字面量的 SQL 形状，不保存绑定参数。
                template = re.sub(r"'(?:''|[^'])*'|\b\d+(?:\.\d+)?\b", "?", sql)
                value.sql.append({"template": template, "ms": (time.perf_counter_ns() - start) / 1e6})

    @measured("SQLite_ms")
    def fetchone(self):
        return super().fetchone()

    @measured("SQLite_ms")
    def fetchall(self):
        return super().fetchall()

    @measured("SQLite_ms")
    def fetchmany(self, *args):
        return super().fetchmany(*args)

    @measured("SQLite_ms")
    def __next__(self):
        return super().__next__()

    @measured("SQLite_ms")
    def close(self):
        return super().close()


class TimedConnection(sqlite3.Connection):
    def cursor(self, factory=TimedCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, parameters):
        return self.cursor().executemany(sql, parameters)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    @measured("SQLite_ms")
    def commit(self):
        with span("sqlite_commit_ms"):
            return super().commit()

    @measured("SQLite_ms")
    def rollback(self):
        return super().rollback()

    @measured("SQLite_ms")
    def close(self):
        return super().close()


def sql_connect(path, **kwargs):
    if current() is None or not current().deep:
        return sqlite3.connect(path, **kwargs)
    add("sqlite_connect_count")
    with span("SQLite_ms"):
        return sqlite3.connect(path, factory=TimedConnection, **kwargs)


def install(app):
    from flask import g, request

    @app.before_request
    def begin():
        if request.path not in ("/api/v1/feed", "/api/v1/feed/refresh") or os.environ.get("BILIPERSONAL_FEED_PERF") != "1":
            return
        g.feed_perf_context = trace(operation="refresh" if request.method == "POST" else "load")
        g.feed_perf_trace = g.feed_perf_context.__enter__()

    @app.after_request
    def finish(response):
        value = getattr(g, "feed_perf_trace", None)
        if value is not None:
            value.info["status_code"] = response.status_code
            response.headers["X-Feed-Trace-Id"] = value.info["trace_id"]
            g.feed_perf_context.__exit__(None, None, None)
            g.feed_perf_trace = None
        return response

    @app.teardown_request
    def teardown(error):
        if getattr(g, "feed_perf_trace", None) is not None:
            if error:
                g.feed_perf_trace.info["error_type"] = type(error).__name__
            g.feed_perf_context.__exit__(None, None, None)
            g.feed_perf_trace = None
