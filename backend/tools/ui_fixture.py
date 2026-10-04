"""浏览器验收专用本地副本，禁止 Bilibili 网络与后台任务。"""
from contextlib import closing
from pathlib import Path
import sqlite3

from backend.config import DB_PATH
from backend.storage import database as db
from backend.storage.migrate_v032 import upgrade
from backend.services import filter_service
from backend.services.bilibili_service import bili
from backend.services.interest_profile import publish
from backend.app import create_app


if __name__ == "__main__":
    path = Path(".tmp/v032/ui-fixture.db").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(Path(DB_PATH).as_uri()+"?mode=ro", uri=True)) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        upgrade(conn)
        conn.commit()
    db.DB_PATH = filter_service.DB_PATH = str(path)
    db.DATA_DIR = str(path.parent)
    bili._cookie = "ui-fixture"
    bili.profile = lambda: {"mid": 1, "name": "本地验收", "face": "", "level": 1, "vip": False}
    def offline(*args, **kwargs):
        raise AssertionError("UI 验收禁止 Bilibili HTTP")
    bili.get = offline
    app = create_app(start_background=False)
    publish()
    app.run(host="127.0.0.1", port=8346, debug=False, use_reloader=False)
