"""
v0.2 后端入口：Flask 只提供 /api/v1 JSON API，并托管 React 构建产物（frontend/dist）。
开发时前端使用 Vite dev server（5173），通过代理访问本服务。
"""

import os

from flask import Flask, jsonify, send_from_directory

from backend.config import FRONTEND_DIST
from backend.routes import auth, feed, feedback, system, user
from backend.services.bilibili_service import bili
from backend.services.recommendation_service import recommendation
from backend.services.training_service import training
from backend.storage.database import init_db
from backend.services import feed_performance as perf


def create_app(start_background=True):
    init_db()
    app = Flask(__name__, static_folder=None)
    app.json.ensure_ascii = False
    perf.install(app)

    for module in (auth, feed, feedback, system, user):
        app.register_blueprint(module.bp)

    @app.errorhandler(404)
    def not_found(e):
        return jsonify({"error": "not_found", "message": "Not Found"}), 404

    # React SPA：非 /api 路径都返回 index.html
    @app.get("/", defaults={"path": ""})
    @app.get("/<path:path>")
    def spa(path):
        if path.startswith("api/"):
            return not_found(None)
        full = os.path.join(FRONTEND_DIST, path)
        if path and os.path.isfile(full):
            return send_from_directory(FRONTEND_DIST, path)
        if not os.path.exists(os.path.join(FRONTEND_DIST, "index.html")):
            return (
                "前端尚未构建：请在 frontend/ 目录执行 npm install && npm run build，"
                "或使用 npm run dev 打开 http://127.0.0.1:5173",
                503,
            )
        return send_from_directory(FRONTEND_DIST, "index.html")

    if start_background and bili.has_cookie:
        # 启动即在后台准备模型与候选池，首页无需等待训练
        training.ensure_started()
        recommendation.warmup()

    return app
