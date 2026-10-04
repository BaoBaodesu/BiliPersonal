"""常驻的请求预算与传输准入，不依赖诊断开关。"""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import time
import json
import threading

from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


_hour_lock = threading.Lock()
_current = ContextVar("bili_request_scope", default=None)


class BudgetEnded(Exception):
    """尚未发送的请求已到预算、停止或风控边界。"""


def current():
    return _current.get() or {}


@contextmanager
def scope(background=None, total=None, details=None, recalls=None, timeout=None, stop=None, candidate=False, purpose=None):
    value = dict(current())
    if background is not None:
        value["background"] = background
    if total is not None:
        value["budget"] = {"total": total, "details": details, "recalls": recalls,
                           "deadline": time.perf_counter() + timeout if timeout is not None else None}
    if stop is not None:
        value["stop"] = stop
    value["candidate"] = value.get("candidate", False) or candidate
    if purpose is not None:
        value["purpose"] = purpose
    elif candidate and "purpose" not in value:
        value["purpose"] = "candidate"
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


@contextmanager
def managed(url):
    value = {**current(), "managed": True, "url": url}
    token = _current.set(value)
    try:
        yield
    finally:
        _current.reset(token)


def check(value, rate_limited=False, sending=True):
    if value.get("stop") and value["stop"].is_set():
        raise BudgetEnded("stopped")
    if value.get("candidate") and rate_limited:
        raise BudgetEnded("rate_limited")
    budget = value.get("budget")
    if budget:
        detail = "/view/detail" in value.get("url", "")
        if sending and (budget["total"] <= 0 or (budget.get("details" if detail else "recalls") is not None and budget["details" if detail else "recalls"] <= 0)):
            raise BudgetEnded("attempt_budget")
        if budget["deadline"] is not None and time.perf_counter() >= budget["deadline"]:
            raise BudgetEnded("deadline")


def consume(value):
    if value.get("purpose") in ("candidate", "search") and value.get("background"):
        from backend.storage.database import connect
        with _hour_lock, connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT value FROM app_state WHERE key='requests:candidate_hour'").fetchone()
            attempts = [v for v in (json.loads(row[0]) if row else []) if v[0] > time.time()-3600]
            search = value.get("purpose") == "search" and "/view/detail" not in value.get("url", "")
            if len(attempts) >= 240 or search and sum(v[1] for v in attempts) >= 40:
                raise BudgetEnded("hour_budget")
            attempts.append([time.time(), search])
            try:
                conn.execute("INSERT INTO app_state VALUES('requests:candidate_hour',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (json.dumps(attempts), time.time()))
                totals = conn.execute("SELECT value FROM app_state WHERE key='requests:candidate_totals'").fetchone()
                totals = json.loads(totals[0]) if totals else {"attempts": 0, "search": 0}
                totals["attempts"] += 1
                totals["search"] += int(search)
                conn.execute("INSERT INTO app_state VALUES('requests:candidate_totals',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (json.dumps(totals), time.time()))
                conn.commit()
            except Exception as error:
                raise BudgetEnded("budget_persistence_failed") from error
    budget = value.get("budget")
    if budget:
        budget["total"] -= 1
        key = "details" if "/view/detail" in value.get("url", "") else "recalls"
        if budget[key] is not None:
            budget[key] -= 1


def background(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with scope(background=True, stop=kwargs.get("stop"), candidate=True):
            return fn(*args, **kwargs)
    return wrapped


class ManagedRetry(Retry):
    def __init__(self, *args, gate=None, **kwargs):
        self.gate = gate
        super().__init__(*args, **kwargs)

    def new(self, **kwargs):
        return super().new(gate=self.gate, **kwargs)

    def sleep(self, response=None):
        if not current().get("managed"):
            return super().sleep(response)
        delay = self.get_retry_after(response) if self.respect_retry_after_header and response is not None else None
        self.gate(delay=delay or self.get_backoff_time())


class ManagedAdapter(HTTPAdapter):
    def __init__(self, gate, **kwargs):
        self.gate = gate
        super().__init__(**kwargs)

    def send(self, request, **kwargs):
        if current().get("managed"):
            self.gate(url=request.url)
        return super().send(request, **kwargs)


def collection(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        with scope(background=True, stop=kwargs.get("stop"), candidate=True, purpose="collection"):
            return fn(*args, **kwargs)
    return wrapped
