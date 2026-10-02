"""v0.3.1 增量迁移：只新增表和列，保留历史与模型归属。"""


def migrate(conn):
    for table, columns in {
        "recommendation_history": {"source": "TEXT"},
        "feedback": {"reason": "TEXT"},
    }.items():
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS followings (
        mid INTEGER PRIMARY KEY, name TEXT NOT NULL, synced_at REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS interest_penalties (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, key TEXT NOT NULL,
        reason TEXT, feedback_id INTEGER NOT NULL, created_at REAL NOT NULL,
        expires_at REAL NOT NULL, revoked_at REAL,
        FOREIGN KEY(feedback_id) REFERENCES feedback(id)
    );
    CREATE INDEX IF NOT EXISTS idx_interest_penalties_active
    ON interest_penalties(kind, key, expires_at);
    """)
