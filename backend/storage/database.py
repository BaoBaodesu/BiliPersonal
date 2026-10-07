"""
SQLite 本地存储：单用户本地应用，每次操作使用独立连接（WAL 模式，线程安全）。
注意：数据库中绝不保存 Cookie。
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from contextvars import ContextVar

from backend.config import DATA_DIR, DB_PATH
from backend.storage.migrations import SCHEMA
from backend.services import feed_performance as perf

_reader = ContextVar("database_snapshot_reader", default=None)


@contextmanager
def connect():
    if _reader.get() is not None:
        yield _reader.get()
        return
    conn = perf.sql_connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def read_snapshot():
    """盲评捕获共用一个只读事务，嵌套读取不另开连接或提交。"""
    with connect() as conn:
        conn.execute("BEGIN")
        conn.execute("PRAGMA query_only=ON")
        token = _reader.set(conn)
        try:
            yield conn
        finally:
            _reader.reset(token)


def init_db():
    if os.path.isfile(DB_PATH):
        with connect() as conn:
            if "source" in {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}:
                raise RuntimeError("请先停止服务并执行 python -m backend.tools.candidate_migration upgrade；该命令先备份和副本演练")
    os.makedirs(DATA_DIR, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        from backend.storage.migrate_v03 import migrate
        migrate(conn)
        from backend.storage.migrate_v031 import migrate
        migrate(conn)
        from backend.storage.migrate_v032 import upgrade
        upgrade(conn)
        from backend.storage.migrate_v034 import upgrade as blind_upgrade
        blind_upgrade(conn)


def now():
    return time.time()


# ---------------- app_state（键值状态） ----------------


def get_state(key, default=None):
    with connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(key, value):
    with connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (key, json.dumps(value, ensure_ascii=False), now()),
        )


def rows_to_dicts(rows):
    return [dict(r) for r in rows]
