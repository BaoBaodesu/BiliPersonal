"""
数据库表结构。使用 CREATE IF NOT EXISTS，启动时幂等执行。
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at REAL NOT NULL
);

-- 候选池：source = hot / rcmd，data 为与 historyVideo.json 同结构的视频 JSON
CREATE TABLE IF NOT EXISTS candidates (
    bvid TEXT NOT NULL,
    source TEXT NOT NULL,
    data TEXT NOT NULL,
    created_at REAL NOT NULL,
    last_refresh_at REAL NOT NULL,
    PRIMARY KEY (bvid, source)
);

-- 已展示视频：同一 Feed 在窗口期内不重复
CREATE TABLE IF NOT EXISTS served_videos (
    bvid TEXT NOT NULL,
    feed_type TEXT NOT NULL,
    first_served_at REAL NOT NULL,
    last_served_at REAL NOT NULL,
    times INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (bvid, feed_type)
);

-- Feed Cache：每次“换一批”生成一个 stream，按页保存，保证分页幂等与二次打开秒开
CREATE TABLE IF NOT EXISTS feed_cache (
    stream_id TEXT NOT NULL,
    feed_type TEXT NOT NULL,
    category TEXT NOT NULL,
    page INTEGER NOT NULL,
    items TEXT NOT NULL,
    has_more INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (stream_id, page)
);
CREATE INDEX IF NOT EXISTS idx_feed_cache_type ON feed_cache(feed_type, category, created_at);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid TEXT NOT NULL,
    action TEXT NOT NULL,
    video TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_action ON feedback(action, bvid);

CREATE TABLE IF NOT EXISTS blocked_ups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mid INTEGER,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_keywords (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keyword TEXT NOT NULL UNIQUE,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

-- v0.2.1 统一过滤规则表：标题关键词 / UP 主名称关键词 / 未来的 tag 规则
-- 与 blocked_ups 并存：blocked_ups 负责按 mid 精确屏蔽，本表负责关键词包含匹配
CREATE TABLE IF NOT EXISTS filter_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    target_type TEXT NOT NULL,
    -- title / uploader / tag

    keyword TEXT NOT NULL,
    keyword_norm TEXT NOT NULL,

    match_mode TEXT NOT NULL DEFAULT 'contains',
    -- contains / exact / regex

    action TEXT NOT NULL DEFAULT 'hard_block',
    -- hard_block / downrank

    enabled INTEGER NOT NULL DEFAULT 1,

    source TEXT NOT NULL DEFAULT 'manual',
    -- manual / legacy_blocked_keywords / biliblock_import / system

    hit_count INTEGER NOT NULL DEFAULT 0,
    last_hit_at TEXT,

    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_filter_rules_unique
ON filter_rules(target_type, keyword_norm, match_mode);

CREATE INDEX IF NOT EXISTS idx_filter_rules_enabled_target
ON filter_rules(enabled, target_type);

CREATE TABLE IF NOT EXISTS recommendation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bvid TEXT NOT NULL,
    feed_type TEXT NOT NULL,
    title TEXT,
    author TEXT,
    pic TEXT,
    rating REAL,
    rank INTEGER,
    model_version TEXT,
    served_at REAL NOT NULL,
    clicked INTEGER NOT NULL DEFAULT 0,
    feedback TEXT
);
CREATE INDEX IF NOT EXISTS idx_rec_history_bvid ON recommendation_history(bvid);
CREATE INDEX IF NOT EXISTS idx_rec_history_time ON recommendation_history(served_at);
"""
