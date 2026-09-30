"""
SQLite 本地存储：单用户本地应用，每次操作使用独立连接（WAL 模式，线程安全）。
注意：数据库中绝不保存 Cookie。
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

from backend.config import DATA_DIR, DB_PATH
from backend.storage.migrations import SCHEMA


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        from backend.storage.migrate_v03 import migrate
        migrate(conn)


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
