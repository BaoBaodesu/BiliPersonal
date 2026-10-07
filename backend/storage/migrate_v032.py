"""候选身份升级与结构降级；调用方必须先停止服务并使用 SQLite backup。"""
import json
import time

SOURCE_ORDER = ("follow", "related", "up_archive", "rcmd", "hot", "vertical_search")
REASON_FIELDS = ("seed_bvid", "seed_title", "query", "archive_order", "rcmd_reason", "query_theme", "special_mid", "intent", "entity_id", "dictionary_version")


def body(video):
    return {k: v for k, v in video.items() if k not in (*REASON_FIELDS, "source", "sources", "source_reasons")}


def merge_reasons(existing, video, timestamp):
    reason = {k: video[k] for k in REASON_FIELDS if video.get(k) is not None}
    for item in existing:
        if {k: item[k] for k in REASON_FIELDS if k in item} == reason:
            item["last_recalled_at"] = timestamp
            return sorted(existing, key=lambda r: r["last_recalled_at"])[-20:]
    return (existing + [{**reason, "recalled_at": timestamp, "last_recalled_at": timestamp}])[-20:]


def upgrade(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}
    if "source" not in columns:
        return
    rows = conn.execute("SELECT * FROM candidates").fetchall()
    conn.execute("SAVEPOINT candidates_upgrade")
    try:
        conn.execute("ALTER TABLE candidates RENAME TO candidates_v031")
        conn.execute("CREATE TABLE candidates(bvid TEXT PRIMARY KEY,data TEXT NOT NULL,created_at REAL NOT NULL,updated_at REAL NOT NULL)")
        conn.execute("CREATE TABLE candidate_sources(bvid TEXT NOT NULL REFERENCES candidates(bvid) ON DELETE CASCADE,source TEXT NOT NULL,reasons TEXT NOT NULL,created_at REAL NOT NULL,last_refresh_at REAL NOT NULL,PRIMARY KEY(bvid,source))")
        chosen, earliest = {}, {}
        for row in rows:
            earliest[row["bvid"]] = min(earliest.get(row["bvid"], row["created_at"]), row["created_at"])
            video = json.loads(row["data"])
            priority = (bool(video.get("_detail_complete")), row["last_refresh_at"], -SOURCE_ORDER.index(row["source"]) if row["source"] in SOURCE_ORDER else -99)
            if row["bvid"] not in chosen or priority > chosen[row["bvid"]][0]:
                chosen[row["bvid"]] = (priority, row, video)
        for _, row, video in chosen.values():
            created = earliest[row["bvid"]]
            conn.execute("INSERT INTO candidates VALUES(?,?,?,?)", (row["bvid"], json.dumps(body(video), ensure_ascii=False), created, row["last_refresh_at"]))
        for row in rows:
            conn.execute("INSERT INTO candidate_sources VALUES(?,?,?,?,?)", (row["bvid"], row["source"], json.dumps(merge_reasons([], json.loads(row["data"]), row["last_refresh_at"]), ensure_ascii=False), row["created_at"], row["last_refresh_at"]))
        conn.execute("CREATE INDEX idx_candidate_sources_refresh ON candidate_sources(source,last_refresh_at)")
        if conn.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("候选迁移外键校验失败")
        if conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] != len(chosen) or conn.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0] != len(rows):
            raise ValueError("候选迁移数量校验失败")
        conn.execute("DROP TABLE candidates_v031")
        conn.execute("RELEASE candidates_upgrade")
    except Exception:
        conn.execute("ROLLBACK TO candidates_upgrade")
        conn.execute("RELEASE candidates_upgrade")
        raise


def downgrade(conn):
    if "source" in {r[1] for r in conn.execute("PRAGMA table_info(candidates)")}:
        return
    conn.execute("SAVEPOINT candidates_downgrade")
    try:
        rows = conn.execute("SELECT c.*,s.source,s.reasons,s.last_refresh_at FROM candidates c JOIN candidate_sources s USING(bvid)").fetchall()
        # 仅保存候选关系，不回滚反馈、曝光或流水。
        conn.execute("INSERT OR REPLACE INTO app_state VALUES(?,?,?)", ("candidates:降级备份", json.dumps([dict(r) for r in rows], ensure_ascii=False), time.time()))
        conn.execute("DROP TABLE candidate_sources")
        conn.execute("ALTER TABLE candidates RENAME TO candidates_v032")
        conn.execute("CREATE TABLE candidates(bvid TEXT NOT NULL,source TEXT NOT NULL,data TEXT NOT NULL,created_at REAL NOT NULL,last_refresh_at REAL NOT NULL,PRIMARY KEY(bvid,source))")
        for row in rows:
            reasons = json.loads(row["reasons"])
            video = {**json.loads(row["data"]), **({k: v for k, v in reasons[-1].items() if k in REASON_FIELDS} if reasons else {}), "source": row["source"]}
            conn.execute("INSERT INTO candidates VALUES(?,?,?,?,?)", (row["bvid"], row["source"], json.dumps(video, ensure_ascii=False), row["created_at"], row["last_refresh_at"]))
        conn.execute("DROP TABLE candidates_v032")
        conn.execute("RELEASE candidates_downgrade")
    except Exception:
        conn.execute("ROLLBACK TO candidates_downgrade")
        conn.execute("RELEASE candidates_downgrade")
        raise
