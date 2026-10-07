from flask import Blueprint, jsonify, request

from backend.routes.common import api, login_required, error
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


@bp.get("/sources")
@api
@login_required
def sources():
    from backend.services.source_mixer import settings, source_report
    return jsonify({"settings": settings(), "report": source_report()})


@bp.put("/sources")
@api
@login_required
def set_sources():
    from backend.services.source_mixer import update_settings, source_report
    try:
        value = update_settings(request.get_json(silent=True))
    except ValueError as exception:
        return error("bad_request", str(exception), 400)
    return jsonify({"settings": value, "report": source_report()})


@bp.route("/recommendation-settings", methods=["GET", "PUT"])
@api
@login_required
def recommendation_settings():
    from backend.services.recommendation_policy import settings, update_settings
    return jsonify(settings() if request.method == "GET" else update_settings(request.get_json(silent=True)))


@bp.route("/policy-trial", methods=["GET", "POST"])
@api
@login_required
def policy_trial():
    from backend.services.recommendation_policy import trial_status, trial_action
    return jsonify(trial_status() if request.method == "GET" else trial_action((request.get_json(silent=True) or {}).get("action")))


@bp.route("/policy-reviews", methods=["GET", "POST"])
@api
@login_required
def policy_reviews():
    from backend.services import policy_review as review
    data = request.get_json(silent=True) or {}
    return jsonify(review.list_reviews() if request.method == "GET" else review.create(data.get("candidate"), data.get("request_id")))


@bp.get("/policy-reviews/<experiment_id>")
@api
@login_required
def policy_review_detail(experiment_id):
    from backend.services.policy_review import detail
    return jsonify(detail(experiment_id))


@bp.post("/policy-reviews/<experiment_id>/prepare")
@api
@login_required
def policy_review_prepare(experiment_id):
    from backend.services.policy_review import request_prepare
    return jsonify(request_prepare(experiment_id))


@bp.post("/policy-reviews/<experiment_id>/abort")
@api
@login_required
def policy_review_abort(experiment_id):
    from backend.services.policy_review import abort
    return jsonify(abort(experiment_id, (request.get_json(silent=True) or {}).get("reason")))


@bp.get("/policy-reviews/<experiment_id>/batches/<batch_id>")
@api
@login_required
def policy_review_batch(experiment_id, batch_id):
    from backend.services.policy_review import get_batch
    return jsonify(get_batch(experiment_id, batch_id))


@bp.put("/policy-reviews/<experiment_id>/batches/<batch_id>/items/<item_id>/rating")
@api
@login_required
def policy_review_rate(experiment_id, batch_id, item_id):
    from backend.services.policy_review import rate
    data = request.get_json(silent=True) or {}
    return jsonify(rate(experiment_id, batch_id, item_id, data.get("score"), data.get("revision"), data.get("request_id")))


@bp.post("/policy-reviews/<experiment_id>/batches/<batch_id>/submit")
@api
@login_required
def policy_review_submit(experiment_id, batch_id):
    from backend.services.policy_review import submit
    return jsonify(submit(experiment_id, batch_id))


@bp.get("/policy-reviews/<experiment_id>/report")
@api
@login_required
def policy_review_report(experiment_id):
    from backend.services.policy_review import report
    return jsonify(report(experiment_id))


@bp.post("/policy-reviews/<experiment_id>/trial")
@api
@login_required
def policy_review_trial(experiment_id):
    from backend.services.policy_review import start_trial
    data = request.get_json(silent=True) or {}
    return jsonify(start_trial(experiment_id, data.get("request_id"), data.get("confirm_restore", False)))
