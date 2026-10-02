"""
用户反馈与屏蔽规则。
- 反馈统一记录到 feedback 表（v0.2 只记录，不重训）。
- not_interested / block_up / watched 立即影响后续 Feed；已缓存页面由前端本地移除。
"""

import json

from backend.services.filter_service import (
    TARGET_TITLE,
    FilterError,
    filters,
    _bump_filter_version,
)
from backend.storage.database import connect, now, rows_to_dicts

ACTIONS = ("like", "not_interested", "block_up", "watched", "watch_later", "click", "undo")
# 这些反馈会让视频不再出现在推荐中
HIDING_ACTIONS = ("not_interested", "block_up", "watched")
REASONS = ("uploader", "topic", "clickbait")


class FeedbackError(Exception):
    pass


class FeedbackService:
    def record(self, bvid, action, video=None, context=None):
        if action not in ACTIONS:
            raise FeedbackError(f"unknown action: {action}")
        video = video or {}
        context = context or {}
        if context.get("reason") is not None and (action != "not_interested" or context["reason"] not in REASONS):
            raise FeedbackError("非法不感兴趣原因")
        if context.get("event_id") is not None and (not isinstance(context["event_id"], str) or not 1 <= len(context["event_id"]) <= 128):
            raise FeedbackError("非法反馈事件标识")
        with connect() as conn:
            if context.get("event_id"):
                existing = conn.execute("SELECT bvid,action,recommendation_id,reason FROM feedback WHERE event_id=?", (context["event_id"],)).fetchone()
                if existing:
                    if existing["bvid"] != bvid or existing["action"] != action or existing["recommendation_id"] != context.get("recommendation_id") or existing["reason"] != context.get("reason"):
                        raise FeedbackError("反馈事件标识冲突")
                    return {"ok": True}
            if action == "undo":
                from backend.services.exposure_service import associate
                try:
                    associated = associate(conn, context, bvid)
                except ValueError as e:
                    raise FeedbackError(str(e)) from e
                # 撤销该视频最近一次隐藏类反馈
                row = conn.execute(
                    f"SELECT id, action,blocked_up_id,video,recommendation_id FROM feedback WHERE revoked_at IS NULL AND bvid = ? AND action IN ({','.join('?' * len(HIDING_ACTIONS))}) "
                    "ORDER BY id DESC LIMIT 1",
                    (bvid, *HIDING_ACTIONS),
                ).fetchone()
                if row:
                    conn.execute("UPDATE feedback SET revoked_at=? WHERE id=?", (now(),row["id"]))
                    conn.execute("UPDATE interest_penalties SET revoked_at=? WHERE feedback_id=?", (now(),row["id"]))
                    saved = json.loads(row["video"] or "{}")
                    if row["action"] == "block_up":
                        if row["blocked_up_id"]:
                            conn.execute("DELETE FROM blocked_ups WHERE id=?", (row["blocked_up_id"],))
                        elif row["recommendation_id"] is None and saved.get("author"):
                            conn.execute("DELETE FROM blocked_ups WHERE name=?", (saved["author"],))
                        _bump_filter_version(conn)
                        filters.invalidate()
                    if row["recommendation_id"]:
                        conn.execute("UPDATE recommendation_history SET feedback=NULL WHERE id=?", (row["recommendation_id"],))
                conn.execute("INSERT INTO feedback(bvid,action,video,created_at,event_id,recommendation_id,exposure_id) VALUES(?,'undo',?,?,?,?,?)", (bvid,json.dumps(_brief(video),ensure_ascii=False),now(),context.get("event_id"),context.get("recommendation_id"),associated[1] if associated else None))
                return {"undone": bool(row)}

            from backend.services.exposure_service import associate
            try:
                associated = associate(conn, context, bvid, click=action == "click", action=True)
            except ValueError as e:
                raise FeedbackError(str(e)) from e
            if associated:
                video = {**video, **json.loads(associated[0]["video_snapshot"] or "{}")}
                conn.execute("UPDATE recommendation_history SET " + ("clicked = 1" if action == "click" else "feedback = ?") + " WHERE id = ?",
                             (associated[0]["id"],) if action == "click" else (action, associated[0]["id"]))
            result = conn.execute(
                "INSERT INTO feedback(bvid, action, video, created_at,recommendation_id,exposure_id,event_id,reason) VALUES(?, ?, ?, ?,?,?,?,?)",
                (bvid, action, json.dumps(_brief(video), ensure_ascii=False), now(), context.get("recommendation_id"),
                 associated[1] if associated else None, context.get("event_id"), context.get("reason")),
            )
            if action == "not_interested":
                from backend.config import SOURCE_PENALTY_DAYS
                from backend.services.affinity import affinity, up_key
                from backend.services.filter_service import normalize
                penalties = []
                if context.get("reason") == "topic":
                    tags = video.get("tag") or video.get("tags") or []
                    if tags:
                        penalties.append(("tag", normalize(tags[0])))
                elif context.get("reason") in ("uploader", "clickbait") and up_key(video):
                    penalties.append(("up", up_key(video)))
                if up_key(video) and affinity.is_stranger(video):
                    penalties.append(("expand_up", up_key(video)))
                for kind, key in penalties:
                    conn.execute("INSERT INTO interest_penalties(kind,key,reason,feedback_id,created_at,expires_at) VALUES(?,?,?,?,?,?)",
                                 (kind,key,context.get("reason"),result.lastrowid,now(),now()+SOURCE_PENALTY_DAYS*86400))
            if action == "block_up" and video.get("author"):
                exists = conn.execute(
                    "SELECT 1 FROM blocked_ups WHERE (mid IS NULL AND name = ?) OR mid = ?",
                    (video["author"], video.get("mid")),
                ).fetchone()
                if not exists:
                    blocked = conn.execute(
                        "INSERT INTO blocked_ups(mid, name, created_at) VALUES(?, ?, ?)",
                        (video.get("mid"), video["author"], now()),
                    )
                    conn.execute("UPDATE feedback SET blocked_up_id=? WHERE id=?", (blocked.lastrowid,result.lastrowid))
                    _bump_filter_version(conn)
                    filters.invalidate()
        return {"ok": True}

    def history(self, action=None, limit=100, offset=0):
        sql = "SELECT * FROM feedback WHERE action NOT IN ('click','undo') AND revoked_at IS NULL"
        params = []
        if action:
            sql += " AND action = ?"
            params.append(action)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        with connect() as conn:
            rows = rows_to_dicts(conn.execute(sql, params).fetchall())
        for r in rows:
            r["video"] = json.loads(r["video"]) if r["video"] else {}
        return rows

    def remove(self, feedback_id):
        with connect() as conn:
            row = conn.execute("SELECT action,blocked_up_id FROM feedback WHERE id=? AND revoked_at IS NULL", (feedback_id,)).fetchone()
            conn.execute("UPDATE feedback SET revoked_at=? WHERE id=?", (now(),feedback_id))
            conn.execute("UPDATE interest_penalties SET revoked_at=? WHERE feedback_id=?", (now(),feedback_id))
            if row and row["action"] == "block_up" and row["blocked_up_id"]:
                conn.execute("DELETE FROM blocked_ups WHERE id=?", (row["blocked_up_id"],))
                _bump_filter_version(conn)
                filters.invalidate()

    def watch_later(self):
        return self.history("watch_later", limit=200)

    # ---------------- 屏蔽规则 ----------------
    # v0.2.1 起以 filter_rules 为唯一数据源：
    # 旧的 /filters/keywords 路径映射为 filter_rules(target_type='title')，
    # 不再直接读写 blocked_keywords（该表保留作历史兼容，不再作为数据源）。

    def filters(self):
        keywords = filters.list_rules(target_type=TARGET_TITLE)
        ups = filters.list_blocked_ups()
        return {"keywords": keywords, "ups": ups}

    def add_keyword(self, keyword):
        try:
            return filters.add_rule(keyword, target_type=TARGET_TITLE)
        except FilterError as e:
            raise FeedbackError(str(e)) from e

    def update_keyword(self, keyword_id, enabled):
        filters.update_rule(keyword_id, enabled=enabled)

    def delete_keyword(self, keyword_id):
        filters.delete_rule(keyword_id)

    def add_up(self, name, mid=None):
        return filters.add_blocked_up(name, mid)

    def delete_up(self, up_id):
        filters.delete_blocked_up(up_id)

    def active_rules(self):
        """兼容读取接口：统一由 filter_service 提供，避免出现两套独立数据源。"""
        with connect() as conn:
            hidden = conn.execute(
                f"SELECT DISTINCT bvid FROM feedback WHERE revoked_at IS NULL AND action IN ({','.join('?' * len(HIDING_ACTIONS))})",
                HIDING_ACTIONS,
            ).fetchall()
        rules = filters.load_rules()
        return {
            "keywords": [r["keyword_norm"] for r in rules["title"]],
            "uploader_keywords": [r["keyword_norm"] for r in rules["uploader"]],
            "blocked_mids": rules["blocked_mids"],
            "blocked_names": rules["blocked_names"],
            "hidden_bvids": {r["bvid"] for r in hidden},
        }


def _brief(video):
    keys = ("bvid", "title", "pic", "author", "mid", "duration", "view", "tname", "tag", "tags",
            "like", "favorite", "coin", "reply", "pubdate", "source", "share", "face", "aid", "rcmd_reason",
            "progress", "isliked", "isfaved", "view_at", "fav_time")
    return {k: video.get(k) for k in keys if video.get(k) is not None}


feedback = FeedbackService()
