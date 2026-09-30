"""位置级曝光与主动操作。快速操作不伪造连续一秒可见。"""
import json
import math
import time

from backend.storage.database import connect


def associate(conn, context, bvid=None, click=False, action=False):
    recommendation_id = context.get("recommendation_id")
    if not recommendation_id:
        return None
    row = conn.execute("SELECT * FROM recommendation_history WHERE id = ?", (recommendation_id,)).fetchone()
    if not row or (bvid and row["bvid"] != bvid):
        raise ValueError("推荐位置与视频不匹配")
    view_id, exposure_id = context.get("view_id"), context.get("exposure_id")
    if not isinstance(view_id, str) or not 1 <= len(view_id) <= 128 or not isinstance(exposure_id, str) or not 1 <= len(exposure_id) <= 128:
        raise ValueError("缺少有效曝光标识")
    if row["view_id"] != view_id:
        raise ValueError("推荐浏览批次不匹配")
    existing = conn.execute("SELECT * FROM recommendation_exposures WHERE id = ?", (exposure_id,)).fetchone()
    if existing and (existing["recommendation_id"] != recommendation_id or existing["view_id"] != view_id):
        raise ValueError("曝光标识冲突")
    clock = time.time()
    conn.execute("INSERT OR IGNORE INTO recommendation_exposures(id,recommendation_id,view_id,received_at) VALUES(?,?,?,?)",
                 (exposure_id, recommendation_id, view_id, clock))
    actual = conn.execute("SELECT id FROM recommendation_exposures WHERE recommendation_id=? AND view_id=?",
                          (recommendation_id, view_id)).fetchone()[0]
    if click:
        conn.execute("UPDATE recommendation_exposures SET clicked_at=COALESCE(clicked_at,?),acted_at=COALESCE(acted_at,?) WHERE id=?",
                     (clock, clock, actual))
    elif action:
        conn.execute("UPDATE recommendation_exposures SET acted_at=COALESCE(acted_at,?) WHERE id=?", (clock, actual))
    return dict(row), actual


def record_exposures(items):
    if not isinstance(items, list) or len(items) > 100:
        raise ValueError("曝光批次数量不合法")
    with connect() as conn:
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("曝光格式不合法")
            clock = time.time()
            visible = item.get("visible_at")
            if not isinstance(visible, (float, int)) or not clock - 3 * 86400 <= visible <= clock + 5:
                raise ValueError("可见时间不合法")
            if (not isinstance(item.get("visible_ratio"), (int,float)) or not math.isfinite(item["visible_ratio"])
                    or not .5 <= item["visible_ratio"] <= 1 or not isinstance(item.get("duration_ms"), (int,float))
                    or not math.isfinite(item["duration_ms"]) or item["duration_ms"] < 1000):
                raise ValueError("可见阈值未达到")
            associated = associate(conn, {**item, "exposure_id": item.get("id")})
            if not associated:
                raise ValueError("缺少推荐位置")
            conn.execute("UPDATE recommendation_exposures SET visible_at=COALESCE(visible_at,?) WHERE id=?",
                         (visible, associated[1]))
    return {"ok": True}


def unclicked_summary(clock=None):
    clock = clock or time.time()
    # 原始位置保留；派生观察按 BV / 24h 合并，不生成训练标签。
    with connect() as conn:
        rows = conn.execute("SELECT h.bvid,e.visible_at,e.clicked_at FROM recommendation_exposures e "
                            "JOIN recommendation_history h ON h.id=e.recommendation_id "
                            "WHERE e.visible_at IS NOT NULL ORDER BY e.visible_at").fetchall()
        clicked = {r[0] for r in conn.execute("SELECT bvid FROM feedback WHERE action IN ('click','like','not_interested','watched','watch_later','block_up')")}
        watched = {r[0] for r in conn.execute("SELECT bvid FROM history_events")}
    result = []
    last = {}
    for row in rows:
        if row["bvid"] in clicked or row["bvid"] in watched or row["clicked_at"] or row["visible_at"] > clock - 86400:
            continue
        if row["visible_at"] - last.get(row["bvid"], -86400) < 86400:
            continue
        last[row["bvid"]] = row["visible_at"]
        result.append({"bvid": row["bvid"], "visible_at": row["visible_at"], "window_end": row["visible_at"] + 86400})
    return result
