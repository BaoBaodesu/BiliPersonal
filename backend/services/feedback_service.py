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
)
from backend.storage.database import connect, now, rows_to_dicts

ACTIONS = ("like", "not_interested", "block_up", "watched", "watch_later", "click", "undo")
# 这些反馈会让视频不再出现在推荐中
HIDING_ACTIONS = ("not_interested", "block_up", "watched")


class FeedbackError(Exception):
    pass


class FeedbackService:
    def record(self, bvid, action, video=None):
        if action not in ACTIONS:
            raise FeedbackError(f"unknown action: {action}")
        video = video or {}
        with connect() as conn:
            if action == "undo":
                # 撤销该视频最近一次隐藏类反馈
                row = conn.execute(
                    f"SELECT id, action FROM feedback WHERE bvid = ? AND action IN ({','.join('?' * len(HIDING_ACTIONS))}) "
                    "ORDER BY id DESC LIMIT 1",
                    (bvid, *HIDING_ACTIONS),
                ).fetchone()
                if row:
                    conn.execute("DELETE FROM feedback WHERE id = ?", (row["id"],))
                    if row["action"] == "block_up" and video.get("author"):
                        conn.execute("DELETE FROM blocked_ups WHERE name = ?", (video["author"],))
                    conn.execute(
                        "UPDATE recommendation_history SET feedback = NULL WHERE bvid = ?", (bvid,)
                    )
                return {"undone": bool(row)}

            if action == "click":
                conn.execute("UPDATE recommendation_history SET clicked = 1 WHERE bvid = ?", (bvid,))
            else:
                conn.execute(
                    "UPDATE recommendation_history SET feedback = ? WHERE bvid = ?", (action, bvid)
                )
            conn.execute(
                "INSERT INTO feedback(bvid, action, video, created_at) VALUES(?, ?, ?, ?)",
                (bvid, action, json.dumps(_brief(video), ensure_ascii=False), now()),
            )
            if action == "block_up" and video.get("author"):
                exists = conn.execute(
                    "SELECT 1 FROM blocked_ups WHERE name = ? OR (mid IS NOT NULL AND mid = ?)",
                    (video["author"], video.get("mid")),
                ).fetchone()
                if not exists:
                    conn.execute(
                        "INSERT INTO blocked_ups(mid, name, created_at) VALUES(?, ?, ?)",
                        (video.get("mid"), video["author"], now()),
                    )
        return {"ok": True}

    def history(self, action=None, limit=100, offset=0):
        sql = "SELECT * FROM feedback WHERE action != 'click'"
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
            conn.execute("DELETE FROM feedback WHERE id = ?", (feedback_id,))

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
                f"SELECT DISTINCT bvid FROM feedback WHERE action IN ({','.join('?' * len(HIDING_ACTIONS))})",
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
    keys = ("bvid", "title", "pic", "author", "mid", "duration", "view", "tname")
    return {k: video.get(k) for k in keys if video.get(k) is not None}


feedback = FeedbackService()
