from flask import Blueprint, jsonify, request

from backend.routes.common import api, error
from backend.services.bilibili_service import LoginExpired, bili
from backend.services.cache_service import pool
from backend.services.recommendation_service import recommendation
from backend.services.training_service import training

bp = Blueprint("auth", __name__, url_prefix="/api/v1/auth")


@bp.post("/qrcode")
@api
def qrcode_generate():
    return jsonify(bili.qrcode_generate())


@bp.get("/qrcode/status")
@api
def qrcode_status():
    qrcode_key = request.args.get("qrcode_key")
    if not qrcode_key:
        return error("bad_request", "Missing qrcode_key", 400)
    status = bili.qrcode_poll(qrcode_key)
    if status == "success":
        # 新登录：清空上一个账号的候选池与 Feed 缓存，后台准备模型与首页推荐
        training.reset()
        pool.clear()
        recommendation.clear_cache()
        training.ensure_started(force_refresh=True)
        recommendation.warmup()
    return jsonify({"status": status})


@bp.get("/status")
@api
def status():
    if not bili.has_cookie:
        return jsonify({"logged_in": False})
    try:
        profile = bili.profile()
    except LoginExpired:
        return jsonify({"logged_in": False, "expired": True})
    return jsonify({"logged_in": True, "user": profile})


@bp.post("/logout")
@api
def logout():
    bili.clear_cookie()
    training.reset()
    pool.clear()
    recommendation.clear_cache()
    return jsonify({"success": True})
