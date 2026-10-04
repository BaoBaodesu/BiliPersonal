from flask import Blueprint, jsonify, request

from backend.routes.common import api, error, login_required
from backend.services.bilibili_service import bili
from backend.services.filter_service import filters
from backend.services.recommendation_service import FeedError, recommendation
from backend.services import feed_performance as perf

bp = Blueprint("feed", __name__, url_prefix="/api/v1")


@bp.post("/feed/exposures")
@api
@login_required
def exposures():
    from backend.services.exposure_service import record_exposures
    try:
        return jsonify(record_exposures((request.get_json(silent=True) or {}).get("items")))
    except ValueError as e:
        return error("bad_request", str(e), 400)


@bp.get("/feed")
@api
@login_required
def get_feed():
    try:
        return perf.json_response(recommendation.get_feed(
            request.args.get("type", "for_you"),
            request.args.get("category", "all"),
            request.args.get("cursor"),
            request.args.get("limit", 12),
            request.args.get("view_id"),
        ))
    except FeedError as e:
        return error("bad_request", str(e), 400)


@bp.post("/feed/refresh")
@api
@login_required
def refresh_feed():
    body = request.get_json(silent=True) or {}
    try:
        return perf.json_response(recommendation.refresh(
            body.get("type", "for_you"), body.get("category", "all"), body.get("limit", 12), body.get("view_id")
        ))
    except FeedError as e:
        return error("bad_request", str(e), 400)


@bp.get("/feed/categories")
@api
@login_required
def categories():
    return jsonify({"items": recommendation.categories()})


@bp.get("/feed/explain/<bvid>")
@api
@login_required
def explain(bvid):
    try:
        return jsonify(recommendation.explain(bvid, request.args.get("recommendation_id", type=int)))
    except FeedError as e:
        return error("not_found", str(e), 404)


@bp.get("/feed/history")
@api
@login_required
def recommendation_history():
    return jsonify(recommendation.history(
        min(int(request.args.get("limit", 50)), 200),
        int(request.args.get("offset", 0)),
        request.args.get("type") or None,
    ))


@bp.get("/search")
@api
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"items": [], "page": 1, "num_pages": 0})
    result = bili.search(q, int(request.args.get("page", 1)))
    # 搜索结果同样应用统一过滤规则（不应用 served 去重）；命中计入规则的 hit_count
    result["items"], _stats = filters.filter_candidates(result["items"], record=True)
    result["items"] = list({v["bvid"]: v for v in result["items"]}.values())
    return jsonify(result)
