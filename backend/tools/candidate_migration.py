"""停止服务后的候选结构迁移。python -m backend.tools.candidate_migration upgrade|downgrade。"""
import argparse
from contextlib import closing
from pathlib import Path
import socket
import sqlite3
import time

from backend.config import DB_PATH
from backend.storage.migrate_v032 import upgrade, downgrade


def migrate(path, direction):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8345)) == 0:
            raise ValueError("请先停止 8345 端口上的服务")
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("数据库不存在")
    backup = path.with_name(path.name+f".{direction}.{time.time_ns()}.backup")
    rehearsal = backup.with_suffix(".rehearsal")
    with closing(sqlite3.connect(path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    with closing(sqlite3.connect(backup)) as source, closing(sqlite3.connect(rehearsal)) as target:
        source.backup(target)
    operation = upgrade if direction == "upgrade" else downgrade
    for target in (rehearsal, path):
        with closing(sqlite3.connect(target)) as conn, conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            operation(conn)
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or conn.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("迁移完整性校验失败")
    return {"database": str(path), "backup": str(backup), "rehearsal": str(rehearsal), "direction": direction}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("upgrade", "downgrade"))
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()
    print(migrate(args.db, args.direction))
