"""观看、收藏和 UP 亲和度分别计算，不修改训练标签。"""
import json
import statistics
import time
from collections import Counter

from backend.storage.database import connect
from backend.services import feed_performance as perf


def up_key(video):
    return str(video.get("mid") or video.get("author") or "")


def progress_ratio(video):
    if video.get("source") == "favorite":
        return 0
    if video.get("progress") == -1:
        return 1
    return min(1, max(0, (video.get("progress") or 0) / video["duration"])) if video.get("duration", 0) else 0


class Affinity:
    def history(self):
        """合并同一 BV 的证据，收藏不覆盖观看进度，画像不依赖旧 JSON。"""
        with connect() as conn:
            rows = conn.execute("SELECT * FROM history_events ORDER BY observed_at,id").fetchall()
            likes = conn.execute("SELECT bvid,video,created_at FROM feedback WHERE action='like' AND revoked_at IS NULL ORDER BY id").fetchall()
        videos = {}
        for row in rows:
            video = json.loads(row["video"])
            previous = videos.get(row["bvid"], {})
            if row["source"] == "favorite" and previous.get("source") == "history":
                video = {**previous, "isfaved": 1, "fav_time": video.get("fav_time")}
            else:
                video["isfaved"] = max(video.get("isfaved") or 0, previous.get("isfaved") or 0)
                video["isliked"] = max(video.get("isliked") or 0, previous.get("isliked") or 0)
            video["observed_at"] = row["observed_at"]
            videos[row["bvid"]] = video
        for row in likes:
            video = {**json.loads(row["video"] or "{}"), **videos.get(row["bvid"], {}), "bvid": row["bvid"], "isliked": 1}
            video["observed_at"] = max(row["created_at"], video.get("observed_at", 0))
            videos[row["bvid"]] = video
        return list(videos.values())

    @perf.measured("affinity_ms")
    def snapshot(self):
        with connect() as conn:
            # 观看窗口与收藏证据独立；同一 BV 的多次观看只计一个视频。
            watched = conn.execute("SELECT * FROM history_events WHERE source='history' ORDER BY COALESCE(watched_at,observed_at) DESC,id DESC LIMIT 1000").fetchall()
            events = conn.execute("SELECT * FROM history_events WHERE isliked=1 OR isfaved=1 OR source='favorite'").fetchall()
            followings = {str(row[0]) for row in conn.execute("SELECT mid FROM followings")}
            actions = conn.execute("SELECT bvid,action,video FROM feedback WHERE revoked_at IS NULL AND action IN ('like','watched')").fetchall()
            seen = {row[0] for row in conn.execute("SELECT DISTINCT bvid FROM history_events WHERE source!='favorite' AND (source='history' OR watched_at IS NOT NULL)")}
            favorites = {row[0] for row in conn.execute("SELECT DISTINCT bvid FROM history_events WHERE source='favorite' OR isfaved=1 OR faved_at IS NOT NULL")}
        ups = {}
        for row in watched:
            video = json.loads(row["video"])
            key = up_key(video)
            if not key:
                continue
            up = ups.setdefault(key, {"mid": video.get("mid"), "name": video.get("author", ""), "videos": set(), "familiar": False, "regular": False})
            ratio = progress_ratio(video)
            if ratio >= .5:
                up["videos"].add(row["bvid"])
            if ratio >= 1:
                up["familiar"] = True
        for row in events:
            video = json.loads(row["video"])
            key = up_key(video)
            if key:
                ups.setdefault(key, {"mid": video.get("mid"), "name": video.get("author", ""), "videos": set(), "familiar": False, "regular": False})["regular"] = True
        for row in actions:
            if row["action"] == "watched":
                seen.add(row["bvid"])
            else:
                video = json.loads(row["video"] or "{}")
                key = up_key(video)
                if key:
                    ups.setdefault(key, {"mid": video.get("mid"), "name": video.get("author", ""), "videos": set(), "familiar": False, "regular": False})["regular"] = True
        for key, up in ups.items():
            up["regular"] = up["regular"] or len(up["videos"]) >= 3
            up["watched_count"] = len(up.pop("videos"))
            up["following"] = key in followings
            up["level"] = "regular" if up["regular"] else "familiar" if up["familiar"] else "stranger"
        return {"ups": ups, "followings": followings, "watched": seen, "favorites": favorites}

    def is_stranger(self, video, snapshot=None):
        snapshot = snapshot or self.snapshot()
        key = up_key(video)
        return key not in snapshot["followings"] and snapshot["ups"].get(key, {}).get("level", "stranger") == "stranger"

    def penalties(self):
        with connect() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM interest_penalties WHERE revoked_at IS NULL AND expires_at>?", (time.time(),))]

    def quality_gate(self, videos, snapshot=None):
        snapshot = snapshot or self.snapshot()
        ratios = [((v.get("like") or 0) + (v.get("coin") or 0) + (v.get("favorite") or 0)) / max(1, v.get("view") or 0) for v in videos]
        global_median = statistics.median(ratios) if ratios else 0
        zones = {}
        for video, ratio in zip(videos, ratios):
            zones.setdefault(video.get("tname", ""), []).append(ratio)
        kept = []
        for video, ratio in zip(videos, ratios):
            video = dict(video)
            video["stranger"] = self.is_stranger(video, snapshot)
            if video["stranger"]:
                group = zones[video.get("tname", "")]
                median = statistics.median(group) if len(group) >= 20 else global_median
                if (video.get("view") or 0) < 1000 or ratio < median * .5:
                    continue
            kept.append(video)
        return kept

    def zones(self):
        return Counter(v["tname"] for v in self.history() if v.get("tname"))

    def seeds(self, category="all", used=(), limit=3):
        from backend.services.filter_service import filters
        videos, _ = filters.filter_candidates(self.history())
        snapshot = self.snapshot()
        from backend.services.interest_profile import preferences
        prefs = preferences()
        videos = [v for v in videos if v["bvid"] not in used and not v.get("downrank") and not v.get("expand_disabled") and
                  (v.get("isliked") or (v.get("isfaved") and prefs.get(v["bvid"], {}).get("purpose", "normal") == "normal") or progress_ratio(v) >= .8) and
                  (category == "all" or v.get("tname") == category or category in ("recent", "discover"))]
        zones = self.zones()
        selected, counts = [], Counter()
        # 逐次按兴趣占比选择分区，避免种子全挤在同一个分区。
        while videos and len(selected) < limit:
            zone = max({v.get("tname", "") for v in videos}, key=lambda z: zones.get(z, 1) / (counts[z] + 1))
            video = max((v for v in videos if v.get("tname", "") == zone), key=lambda v: (v.get("isliked", 0) or 0, v.get("isfaved", 0) or 0, v.get("observed_at", 0)))
            if up_key(video) in snapshot["ups"] or up_key(video) in snapshot["followings"]:
                selected.append(video)
                counts[zone] += 1
            videos.remove(video)
        return selected


affinity = Affinity()
