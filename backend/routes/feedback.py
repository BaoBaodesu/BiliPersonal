from flask import Blueprint, jsonify, request

from backend.routes.common import api, error, login_required
from backend.services.feedback_service import FeedbackError, feedback
from backend.services.filter_service import FilterError, filters

bp = Blueprint("feedback", __name__, url_prefix="/api/v1")


@bp.post("/feedback")
@api
@login_required
def post_feedback():
    body = request.get_json(silent=True) or {}
    if not body.get("bvid"):
        return error("bad_request", "Missing bvid", 400)
    try:
        return jsonify(feedback.record(body["bvid"], body.get("action"), body.get("video")))
    except FeedbackError as e:
        return error("bad_request", str(e), 400)


@bp.get("/feedback/history")
@api
@login_required
def feedback_history():
    return jsonify({"items": feedback.history(
        request.args.get("action") or None,
        min(int(request.args.get("limit", 100)), 500),
        int(request.args.get("offset", 0)),
    )})


@bp.delete("/feedback/<int:feedback_id>")
@api
@login_required
def delete_feedback(feedback_id):
    feedback.remove(feedback_id)
    return jsonify({"success": True})


@bp.get("/filters")
@api
@login_required
def get_filters():
    return jsonify(feedback.filters())


@bp.post("/filters/keywords")
@api
@login_required
def add_keyword():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(feedback.add_keyword(body.get("keyword")))
    except FeedbackError as e:
        return error("bad_request", str(e), 400)


@bp.patch("/filters/keywords/<int:keyword_id>")
@api
@login_required
def update_keyword(keyword_id):
    body = request.get_json(silent=True) or {}
    feedback.update_keyword(keyword_id, body.get("enabled", True))
    return jsonify({"success": True})


@bp.delete("/filters/keywords/<int:keyword_id>")
@api
@login_required
def delete_keyword(keyword_id):
    feedback.delete_keyword(keyword_id)
    return jsonify({"success": True})


@bp.post("/filters/ups")
@api
@login_required
def add_up():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(feedback.add_up(body.get("name"), body.get("mid")))
    except FeedbackError as e:
        return error("bad_request", str(e), 400)


@bp.delete("/filters/ups/<int:up_id>")
@api
@login_required
def delete_up(up_id):
    feedback.delete_up(up_id)
    return jsonify({"success": True})


# ---------------- v0.2.1 统一过滤规则 ----------------
# target_type: title / uploader / tag


@bp.get("/filters/rules")
@api
@login_required
def list_rules():
    enabled = request.args.get("enabled")
    if enabled is not None:
        enabled = enabled.strip().lower() in ("1", "true", "yes")
    return jsonify({
        "items": filters.list_rules(
            request.args.get("target_type") or None,
            enabled,
            request.args.get("q") or None,
        ),
        "summary": filters.summary(),
    })


@bp.post("/filters/rules")
@api
@login_required
def create_rule():
    body = request.get_json(silent=True) or {}
    try:
        return jsonify(filters.add_rule(
            body.get("keyword"),
            target_type=body.get("target_type") or "title",
            match_mode=body.get("match_mode") or "contains",
            action=body.get("action") or "hard_block",
            enabled=body.get("enabled", True),
        ))
    except FilterError as e:
        return error("bad_request", str(e), 400)


@bp.patch("/filters/rules/<int:rule_id>")
@api
@login_required
def update_rule(rule_id):
    body = request.get_json(silent=True) or {}
    payload = {k: body[k] for k in ("enabled", "action", "match_mode", "keyword") if k in body}
    if not payload:
        return error("bad_request", "nothing to update", 400)
    try:
        return jsonify(filters.update_rule(rule_id, **payload))
    except FilterError as e:
        return error("bad_request", str(e), 400)


@bp.delete("/filters/rules/<int:rule_id>")
@api
@login_required
def delete_rule(rule_id):
    if not filters.delete_rule(rule_id):
        return error("not_found", "rule not found", 404)
    return jsonify({"success": True})


@bp.post("/filters/rules/import")
@api
@login_required
def import_rules():
    body = request.get_json(silent=True) or {}
    try:
        result = filters.import_rules(
            body.get("text") or "",
            target_type=body.get("target_type") or "title",
            action=body.get("action") or "hard_block",
        )
    except FilterError as e:
        return error("bad_request", str(e), 400)
    return jsonify(result)


@bp.get("/filters/summary")
@api
@login_required
def filters_summary():
    return jsonify(filters.summary())
