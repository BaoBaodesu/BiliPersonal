"""可追溯训练证据；未点击、屏蔽 UP 不生成训练负样本。"""
import hashlib
import json
import math
import time

from backend.storage.database import connect

PROTOCOL = "interest-binary-v3.1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def timestamp(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and 0 < value <= time.time() + 300 else None
    except (TypeError, ValueError):
        return None


def record_history(video, observed_at=None):
    observed_at = observed_at or time.time()
    video = dict(video)
    source = video.get("source", "history")
    if source == "favorite":
        video["progress"] = None
        video["isfaved"] = 1
    if "tags" in video and "tag" not in video:
        video["tag"] = video["tags"]
    signature = fingerprint(video)
    with connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO history_events(bvid,source,watched_at,faved_at,observed_at,progress,"
            "duration,isliked,isfaved,video,fingerprint) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (video["bvid"], source, timestamp(video.get("view_at")), timestamp(video.get("fav_time")),
             observed_at, video.get("progress"), video.get("duration"), video.get("isliked"),
             video.get("isfaved"), json.dumps(video, ensure_ascii=False), signature),
        )


def label(video, actions=()):
    actions = set(actions)
    if "not_interested" in actions:
        return 0, 2.0, "not_interested"
    if video.get("isliked") or video.get("isfaved") or "like" in actions:
        return 1, 2.0, "interaction"
    progress, duration = video.get("progress"), video.get("duration")
    if video.get("source") != "favorite" and progress is not None and duration and duration > 0:
        if progress == -1:
            return 1, 1.0, "completed"
        if math.isfinite(float(progress)) and 0 <= progress <= duration:
            if progress / duration >= 0.8:
                return 1, 1.0, "completed"
            if progress / duration >= 0.5:
                return 1, 0.5, "watched_half"
            if progress / duration < 0.2:
                return 0, 0.25, "low_progress"
            return None
    if "click" in actions:
        return 1, 0.25, "click"
    return None


def build_samples(cutoff=None):
    cutoff = cutoff or time.time()
    with connect() as conn:
        events = conn.execute("SELECT * FROM history_events WHERE observed_at <= ? ORDER BY observed_at,id",
                              (cutoff,)).fetchall()
        feedback = conn.execute("SELECT * FROM feedback WHERE created_at <= ? AND (revoked_at IS NULL OR revoked_at>?) ORDER BY created_at,id",
                                (cutoff, cutoff)).fetchall()
        candidates = {r["bvid"]: json.loads(r["data"]) for r in conn.execute("SELECT bvid,data FROM candidates WHERE last_refresh_at<=?", (cutoff,))}
    videos, actions, action_times = {}, {}, {}
    for event in events:
        video = json.loads(event["video"])
        previous = videos.get(event["bvid"])
        # 收藏只补充明确正证据，不覆盖真实观看进度。
        if previous and video.get("source") == "favorite":
            video = {**previous, **video, "isfaved": 1, "progress": previous.get("progress"),
                     "view_at":previous.get("view_at"), "source":previous.get("source"),
                     "fav_time":video.get("fav_time") or previous.get("fav_time"),
                     "isliked":max(previous.get("isliked") or 0, video.get("isliked") or 0)}
        elif previous:
            video["isfaved"] = max(previous.get("isfaved") or 0, video.get("isfaved") or 0)
            if video["isfaved"]:
                video["fav_time"] = video.get("fav_time") or previous.get("fav_time")
        video["observed_at"] = event["observed_at"]
        videos[event["bvid"]] = video
    for event in feedback:
        if event["action"] not in ("click", "like", "not_interested") or not event["bvid"]:
            continue
        actions.setdefault(event["bvid"], set()).add(event["action"])
        action_times.setdefault(event["bvid"], {})[event["action"]] = event["created_at"]
        if event["bvid"] not in videos:
            video = json.loads(event["video"] or "{}")
            # 旧反馈特征不足时只采用现有本地完整特征，不猜造 Tag。
            video = {**candidates.get(event["bvid"], {}), **video}
            video["bvid"] = event["bvid"]
            video["observed_at"] = event["created_at"]
            videos[event["bvid"]] = video
    samples = []
    for bvid, video in videos.items():
        result = label(video, actions.get(bvid, ()))
        if result is None or not all(
                video.get(k) is not None for k in ("view", "like", "favorite")):
            continue
        event_at = timestamp(video.get("fav_time")) if result[2] == "interaction" and video.get("isfaved") else timestamp(video.get("view_at"))
        if result[2] in ("not_interested", "click"):
            event_at = action_times[bvid][result[2]]
        elif result[2] == "interaction" and "like" in action_times.get(bvid, {}):
            event_at = max(event_at or 0, action_times[bvid]["like"])
        samples.append({"bvid": bvid, "video": video, "label": result[0], "weight": result[1],
                        "evidence": result[2], "event_at": event_at,
                        "observed_at": video.get("observed_at", cutoff)})
    return sorted(samples, key=lambda s: (s["event_at"] or s["observed_at"], s["bvid"]))


def temporal_split(samples):
    if len({s["bvid"] for s in samples}) != len(samples):
        raise ValueError("训练快照中 BVID 重复")
    samples = sorted(samples, key=lambda s: (s["event_at"] or s["observed_at"], s["bvid"]))
    boundary = int(len(samples) * 0.8)
    return samples[:boundary], samples[boundary:]
