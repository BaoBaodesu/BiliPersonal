"""生产数据库副本迁移演练，包含降级和升级回环；不写生产库。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import statistics

from backend.config import DB_PATH
from backend.storage.migrate_v032 import upgrade, downgrade


def rehearsal():
    path = Path(".tmp/v032/candidate-rehearsal.db").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(DB_PATH).as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.row_factory = sqlite3.Row
        before = conn.execute("SELECT COUNT(*),COUNT(DISTINCT bvid) FROM candidates").fetchone()
        feedback = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        complete = conn.execute("SELECT COUNT(DISTINCT bvid) FROM candidates WHERE json_extract(data,'$._detail_complete')=1").fetchone()[0]
        old_full = [json.loads(row[0]) for row in conn.execute("SELECT data FROM candidates WHERE json_extract(data,'$._detail_complete')=1")]
        conn.execute("PRAGMA foreign_keys=ON")
        upgrade(conn)
        count = conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0]
        relations = conn.execute("SELECT COUNT(*) FROM candidate_sources").fetchone()[0]
        after_complete = conn.execute("SELECT COUNT(*) FROM candidates WHERE json_extract(data,'$._detail_complete')=1").fetchone()[0]
        new_full = [json.loads(row[0]) for row in conn.execute("SELECT data FROM candidates WHERE json_extract(data,'$._detail_complete')=1")]
        assert count == before[1] and relations == before[0] and after_complete == complete
        assert not conn.execute("PRAGMA foreign_key_check").fetchall()
        downgrade(conn)
        assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == relations
        assert conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0] == feedback
        upgrade(conn)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    from backend.storage import database
    from backend.services.affinity import affinity
    previous = database.DB_PATH
    database.DB_PATH = str(path)
    try:
        snapshot = affinity.snapshot()
        ratio = lambda v: ((v.get("like") or 0)+(v.get("coin") or 0)+(v.get("favorite") or 0))/max(1, v.get("view") or 0)
        old_kept = {v["bvid"] for v in affinity.quality_gate(old_full, snapshot)}
        new_kept = {v["bvid"] for v in affinity.quality_gate(new_full, snapshot)}
        quality = {"old_complete_rows": len(old_full), "old_median": statistics.median(map(ratio, old_full)) if old_full else None, "unique_median": statistics.median(map(ratio, new_full)) if new_full else None, "old_kept_bvids": len(old_kept), "unique_kept_bvids": len(new_kept), "newly_kept": len(new_kept-old_kept), "newly_filtered": len(old_kept-new_kept)}
    finally:
        database.DB_PATH = previous
    result = {"database_copy": str(path), "old_rows": before[0], "unique_bvids": count, "relations": relations, "complete_bvids": complete, "complete_rate": complete/count if count else None, "upgrade_downgrade_roundtrip": "passed", "feedback_preserved": feedback, "quality_gate_comparison": quality}
    (path.parent / "migration-report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(rehearsal(), ensure_ascii=False))
