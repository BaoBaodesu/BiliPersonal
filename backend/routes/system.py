from flask import Blueprint, jsonify

from backend.routes.common import api, login_required
from backend.services.bilibili_service import bili
from backend.services.cache_service import pool
from backend.services.recommendation_service import recommendation
from backend.services.training_service import training
from backend.storage.database import connect

bp = Blueprint("system", __name__, url_prefix="/api/v1/system")


@bp.get("/status")
@api
def status():
    s = training.status()
    with connect() as conn:
        has_cache = conn.execute("SELECT 1 FROM feed_cache LIMIT 1").fetchone() is not None
    return jsonify({
        "model": s["model"],
        "training": s["training"],
        "stage": s["stage"],
        "progress": s["progress"],
        "error": s["error"],
        "model_version": s["model_version"],
        "v03": s["v03"],
        "trained_at": s["trained_at"],
        "history_samples": s["history_samples"],
        "history_updated_at": s["history_updated_at"],
        "summary": s["summary"],
        "feed_cache": has_cache,
        "pool_size": pool.count(),
        "rate_limited": bili.rate_limited,
        "logged_in": bili.has_cookie,
    })


@bp.post("/retrain")
@api
@login_required
def retrain():
    started = training.ensure_started(force_refresh=True, force_train=True)
    return jsonify({"started": started, "training": True})


@bp.get("/debug")
@api
@login_required
def debug():
    return jsonify({**recommendation.debug(), "model": training.status()})
