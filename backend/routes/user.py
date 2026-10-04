import time
from collections import Counter
from urllib.parse import urlparse

import requests
from flask import Blueprint, Response, jsonify, request

from backend.services.affinity import affinity, progress_ratio
from backend.routes.common import api, error, login_required
from backend.services.bilibili_service import UA, bili
from backend.services.feedback_service import feedback

bp = Blueprint("user", __name__, url_prefix="/api/v1")


@bp.get("/user/profile")
@api
@login_required
def profile():
    return jsonify(bili.profile())


@bp.get("/user/interests")
@api
@login_required
def interests():
    """兴趣画像读取累计事件，收藏与观看证据分别保留。"""
    history = affinity.history()
    tag_weight = Counter()
    recent_tags = Counter()
    ups = Counter()
    zones = Counter()
    durations = Counter()
    recent = sorted(history, key=lambda h: h.get("view_at", 0), reverse=True)[:15]
    recent_ids = {h["bvid"] for h in recent}

    for h in history:
        # 权重与原版兴趣分数一致：max((点赞+收藏)/2, 进度比例)，最低 0.2
        duration = h.get("duration") or 0
        weight = max((float(h.get("isliked") or 0) + float(h.get("isfaved") or 0)) / 2, progress_ratio(h), 0.2)
        for tag in h.get("tag") or []:
            tag_weight[tag] += weight
            if h["bvid"] in recent_ids:
                recent_tags[tag] += weight
        ups[h.get("author", "")] += 1
        if h.get("tname"):
            zones[h["tname"]] += 1
        minutes = duration / 60
        bucket = "< 5 分钟" if minutes < 5 else "5-20 分钟" if minutes < 20 else "20-60 分钟" if minutes < 60 else "> 1 小时"
        durations[bucket] += 1

    def top(counter, n):
        items = counter.most_common(n)
        peak = items[0][1] if items else 1
        return [{"name": k, "weight": round(v, 2), "score": round(v / peak * 100)} for k, v in items if k]

    from backend.services.interest_profile import snapshot
    from backend.services.interest_analysis import report
    return jsonify({
        "dual_profile": snapshot(),
        "analysis": report(int(request.args.get("days", 7))),
        "samples": len(history),
        "favorites": sum(1 for h in history if h.get("isfaved")),
        "top_tags": top(tag_weight, 30),
        "recent_tags": top(recent_tags, 12),
        "top_ups": top(ups, 12),
        "zones": top(zones, 10),
        "durations": [{"name": k, "count": durations.get(k, 0)}
                      for k in ("< 5 分钟", "5-20 分钟", "20-60 分钟", "> 1 小时")],
    })


@bp.get("/user/history")
@api
@login_required
def watch_history():
    return jsonify(bili.watch_history(
        int(request.args.get("max", 0) or 0), int(request.args.get("view_at", 0) or 0)
    ))


@bp.get("/user/favorites")
@api
@login_required
def favorites():
    media_id = request.args.get("media_id")
    folders = bili.fav_folders()
    if not folders:
        return jsonify({"folders": [], "items": [], "has_more": False})
    media_id = int(media_id) if media_id else folders[0]["id"]
    result = bili.fav_videos(media_id, int(request.args.get("pn", 1)))
    return jsonify({"folders": folders, "media_id": media_id, **result})


@bp.get("/user/watch-later")
@api
@login_required
def watch_later():
    items = []
    seen = set()
    for row in feedback.watch_later():
        if row["bvid"] in seen:
            continue
        seen.add(row["bvid"])
        items.append({**row["video"], "bvid": row["bvid"], "feedback_id": row["id"], "saved_at": row["created_at"]})
    return jsonify({"items": items})


# ---------------- 图片代理（兜底方案，不落盘） ----------------

_ALLOWED_IMG_HOSTS = (".hdslb.com", ".biliimg.com")


@bp.get("/img")
def image_proxy():
    url = request.args.get("url", "")
    host = urlparse(url).hostname or ""
    if not url.startswith("https://") or not host.endswith(_ALLOWED_IMG_HOSTS):
        return error("bad_request", "invalid image url", 400)
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"}, timeout=10)
    except requests.exceptions.RequestException:
        return error("bad_gateway", "image fetch failed", 502)
    return Response(
        r.content,
        status=r.status_code,
        content_type=r.headers.get("Content-Type", "image/jpeg"),
        headers={"Cache-Control": "public, max-age=86400", "Expires": time.strftime(
            "%a, %d %b %Y %H:%M:%S GMT", time.gmtime(time.time() + 86400))},
    )


@bp.route("/user/interest-settings", methods=["GET", "PUT"])
@api
@login_required
def interest_settings():
    from backend.services import interest_profile as profile
    try:
        return jsonify(profile.settings() if request.method == "GET" else profile.update_settings(request.get_json(silent=True)))
    except ValueError as exception:
        return error("bad_request", str(exception), 400)


@bp.put("/user/video-preferences/<bvid>")
@api
@login_required
def video_preferences(bvid):
    from backend.services.interest_profile import set_preference
    try:
        return jsonify(set_preference(bvid, request.get_json(silent=True)))
    except ValueError as exception:
        return error("bad_request", str(exception), 400)


@bp.route("/user/search-history", methods=["GET", "POST", "DELETE"])
@api
@login_required
def search_history():
    from backend.services import interest_profile as profile
    body = request.get_json(silent=True) or {}
    try:
        if request.method == "POST":
            return jsonify({"recorded": profile.record_search(body.get("query"), body.get("event_id"))})
        if request.method == "DELETE":
            profile.delete_search(request.args.get("event_id"))
        return jsonify({"items": profile.search_history(), "enabled": profile.settings()["search_memory"]})
    except ValueError as exception:
        return error("bad_request", str(exception), 400)


@bp.get("/user/video-preferences")
@api
@login_required
def all_video_preferences():
    from backend.services.interest_profile import preferences
    return jsonify(preferences())
