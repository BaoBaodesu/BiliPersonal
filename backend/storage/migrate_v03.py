"""v0.3 增量迁移：旧流水保留空关联，不补造曝光。"""


def migrate(conn):
    for table, columns in {
        "recommendation_history": {"stream_id": "TEXT", "page": "INTEGER", "view_id": "TEXT",
                                   "experiment_id": "TEXT", "arm": "TEXT", "video_snapshot": "TEXT",
                                   "predictions": "TEXT"},
        "feed_cache": {"model_version": "TEXT", "page_limit": "INTEGER", "view_id": "TEXT",
                       "experiment_id": "TEXT", "arm": "TEXT"},
        "feedback": {"recommendation_id": "INTEGER", "exposure_id": "TEXT", "event_id": "TEXT",
                     "revoked_at": "REAL", "blocked_up_id": "INTEGER"},
    }.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS feed_streams (
        stream_id TEXT PRIMARY KEY, view_id TEXT NOT NULL, feed_type TEXT NOT NULL, category TEXT NOT NULL,
        page_limit INTEGER NOT NULL, model_version TEXT NOT NULL, experiment_id TEXT, arm TEXT,
        created_at REAL NOT NULL, UNIQUE(view_id,feed_type,category)
    );
    CREATE TABLE IF NOT EXISTS history_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bvid TEXT NOT NULL, source TEXT NOT NULL,
        watched_at REAL, faved_at REAL, observed_at REAL NOT NULL, progress REAL, duration REAL,
        isliked INTEGER, isfaved INTEGER, video TEXT NOT NULL, fingerprint TEXT NOT NULL UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_history_events_bvid ON history_events(bvid, observed_at);
    CREATE INDEX IF NOT EXISTS idx_history_events_time ON history_events(observed_at);
    CREATE TABLE IF NOT EXISTS model_runs (
        model_version TEXT PRIMARY KEY, protocol TEXT NOT NULL, status TEXT NOT NULL,
        data_cutoff_at REAL NOT NULL, started_at REAL NOT NULL, finished_at REAL,
        data_hash TEXT, artifact_path TEXT, metrics TEXT, config TEXT
    );
    CREATE TABLE IF NOT EXISTS recommendation_exposures (
        id TEXT PRIMARY KEY, recommendation_id INTEGER NOT NULL, view_id TEXT NOT NULL,
        visible_at REAL, clicked_at REAL, acted_at REAL, received_at REAL NOT NULL,
        UNIQUE(recommendation_id, view_id),
        FOREIGN KEY(recommendation_id) REFERENCES recommendation_history(id)
    );
    CREATE INDEX IF NOT EXISTS idx_exposure_time ON recommendation_exposures(visible_at);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_event ON feedback(event_id) WHERE event_id IS NOT NULL;
    CREATE INDEX IF NOT EXISTS idx_feedback_recommendation ON feedback(recommendation_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_rec_stream ON recommendation_history(stream_id, rank);
    """)
