"""双画像规则层：真实行为时间、显式别名和人工控制，与训练标签隔离。"""
import hashlib
import json
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from backend.storage.database import connect, get_state, set_state
from backend.services.affinity import progress_ratio
from backend.services.training_data import timestamp

_lock = threading.RLock()
DAY = 86400


def normalize(value):
    return str(value).strip().casefold()


def settings():
    return {"themes": {}, "aliases": {}, "queries": {}, "query_blacklist": [], "search_memory": False, "special_ups": {}, **get_state("interests:settings", {})}


def update_settings(changes):
    if not isinstance(changes, dict) or set(changes)-set(settings()):
        raise ValueError("未知兴趣设置")
    value = {**settings(), **changes}
    if type(value["search_memory"]) is not bool or not all(isinstance(value[k], dict) for k in ("themes", "aliases", "queries", "special_ups")):
        raise ValueError("非法兴趣设置")
    themes = {}
    for name, config in value["themes"].items():
        if not normalize(name) or not isinstance(config, dict) or config.get("state", "auto") not in ("auto", "fixed", "paused", "excluded"):
            raise ValueError("非法主题状态")
        if not all(isinstance(config.get(k, []), list) and all(isinstance(w, str) and w.strip() for w in config.get(k, [])) for k in ("words", "queries")):
            raise ValueError("主题匹配词必须是文字列表")
        themes[normalize(name)] = config
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value["aliases"].items()):
        raise ValueError("别名必须是文字关系")
    aliases = {normalize(k): normalize(v) for k, v in value["aliases"].items()}
    if any(not k or not v or v in aliases for k, v in aliases.items()):
        raise ValueError("别名须直接指向规范主题，不能循环")
    for query, config in value["queries"].items():
        if not query.strip() or not isinstance(config, dict) or type(config.get("paused", False)) is not bool:
            raise ValueError("非法查询设置")
    if not isinstance(value["query_blacklist"], list) or not all(isinstance(v, str) for v in value["query_blacklist"]):
        raise ValueError("查询黑名单必须是文字列表")
    for mid, config in value["special_ups"].items():
        if not str(mid).isdigit() or not isinstance(config, dict) or (config.get("enabled") is not None and type(config.get("enabled")) is not bool) or not isinstance(config.get("queries", []), list) or not all(isinstance(w, str) and w.strip() for w in config.get("queries", [])):
            raise ValueError("非法特别关注设置")
    value.update(themes=themes, aliases=aliases)
    with _lock:
        set_state("interests:settings", value)
        publish()
    return value


def matched(video, config):
    tags = video.get("tag", video.get("tags", [])) or []
    names = {config["aliases"].get(normalize(t), normalize(t)) for t in tags}
    title = normalize(video.get("title", ""))
    names.update(name for name, theme in config["themes"].items() if any(normalize(w) in title for w in theme.get("words", [])))
    return names


def preferences():
    return get_state("interests:videos", {})


def set_preference(bvid, changes):
    if not isinstance(changes, dict) or set(changes)-{"purpose", "allow_replay", "replay_days"}:
        raise ValueError("未知视频偏好")
    with _lock:
        values = preferences()
        value = {"purpose": "normal", "allow_replay": False, "replay_days": 30, **values.get(bvid, {}), **changes}
        if value["purpose"] not in ("normal", "utility", "support") or type(value["allow_replay"]) is not bool or value["replay_days"] not in (7, 30):
            raise ValueError("非法收藏用途或回看周期")
        values[bvid] = value
        set_state("interests:videos", values)
        publish()
        return value


def search_history():
    return [v for v in get_state("interests:search_history", []) if v["at"] > time.time()-30*DAY][-200:]


def record_search(query, event_id):
    if not isinstance(query, str) or not query.strip() or len(query) > 200 or not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
        raise ValueError("搜索提交需要关键词和事件 ID")
    with _lock:
        if not settings()["search_memory"]:
            return False
        history = search_history()
        if any(v["event_id"] == event_id for v in history):
            return False
        history.append({"query": query.strip(), "event_id": event_id, "at": time.time()})
        set_state("interests:search_history", history[-200:])
        return True


def delete_search(event_id=None):
    with _lock:
        set_state("interests:search_history", [v for v in search_history() if v["event_id"] != event_id] if event_id else [])
        publish()


def publish(clock=None):
    """后台发布；抓取时间与用途标注时间从不作为行为日期。"""
    clock = clock or time.time()
    with _lock:
        config, prefs = settings(), preferences()
        with connect() as conn:
            history = conn.execute("SELECT * FROM history_events").fetchall()
            actions = conn.execute("SELECT bvid,action,video,created_at FROM feedback WHERE revoked_at IS NULL AND action IN ('like','click')").fetchall()
        evidence = defaultdict(list)

        def add(video, kind, weight, at, long=True):
            for name in matched(video, config):
                evidence[name].append({"bvid": video["bvid"], "kind": kind, "weight": weight, "at": timestamp(at), "long": long})

        for row in history:
            video = json.loads(row["video"])
            ratio = progress_ratio(video)
            if row["source"] != "favorite" and ratio >= .5:
                add(video, "watch", 1 if ratio >= 1 else .5, row["watched_at"])
            if row["isliked"]:
                add(video, "like", 2, video.get("liked_at"))
            if row["isfaved"] or row["source"] == "favorite":
                purpose = prefs.get(row["bvid"], {}).get("purpose", "normal")
                if purpose != "support":
                    add(video, "favorite", 2 if purpose == "normal" else .25, row["faved_at"], purpose == "normal")
        for row in actions:
            video = {**json.loads(row["video"] or "{}"), "bvid": row["bvid"]}
            add(video, row["action"], 2 if row["action"] == "like" else .1, row["created_at"], row["action"] == "like")
        if config["search_memory"]:
            seen = set()
            for item in search_history():
                name = config["aliases"].get(normalize(item["query"]), normalize(item["query"]))
                day = int((item["at"]+8*3600)//DAY)
                if name in evidence or name in config["themes"]:
                    if (name, day) not in seen:
                        evidence[name].append({"bvid": "search:"+str(day), "kind": "search", "weight": .25, "at": item["at"], "long": False})
                        seen.add((name, day))
        topics = []
        for name in sorted(set(evidence) | set(config["themes"])):
            values = evidence[name]
            watch = [e for e in values if e["kind"] == "watch" and e["at"]]
            explicit = [e for e in values if e["kind"] in ("like", "favorite") and e["long"] and e["at"]]
            watch_days = {int((e["at"]+8*3600)//DAY) for e in watch}
            def span(rows):
                return max(e["at"] for e in rows)-min(e["at"] for e in rows) if rows else 0
            qualified_watch = len({e["bvid"] for e in watch}) >= 5 and len(watch_days) >= 3 and span(watch) >= 30*DAY
            qualified_explicit = len({e["bvid"] for e in explicit}) >= 3 and span(explicit) >= 30*DAY
            strongest, short = {}, {}
            for e in values:
                if e["long"]:
                    strongest[e["bvid"]] = max(strongest.get(e["bvid"], 0), e["weight"])
                if e["at"] and 0 <= clock-e["at"] <= 14*DAY:
                    decayed = e["weight"]*2**(-(clock-e["at"])/(3*DAY))
                    if decayed > short.get(e["bvid"], (0, 0))[0]:
                        short[e["bvid"]] = (decayed, int((e["at"]+8*3600)//DAY))
            daily = defaultdict(list)
            search_contribution = 0
            for bvid, (weight, day) in short.items():
                if bvid.startswith("search:"):
                    search_contribution += weight
                else:
                    daily[day].append(weight)
            state = config["themes"].get(name, {}).get("state", "auto")
            qualified = qualified_watch or qualified_explicit
            topics.append({"name": name, "state": state, "qualified": qualified, "qualification": "watch" if qualified_watch else "explicit" if qualified_explicit else None,
                           "L": 1 if state == "fixed" else min(1, sum(strongest.values())/6) if qualified else 0,
                           "S": min(1, (search_contribution+sum(sum(sorted(v, reverse=True)[:3]) for v in daily.values()))/6), "watch_bvids": len({e["bvid"] for e in watch}),
                           "watch_days": len(watch_days), "watch_span_days": span(watch)/DAY, "explicit_bvids": len({e["bvid"] for e in explicit}),
                           "explicit_span_days": span(explicit)/DAY, "last_activity": max((e["at"] or 0 for e in values), default=0) or None,
                           "evidence": list({(e["bvid"], e["kind"], e["at"]): e for e in values}.values()), "half_life_days": 3})
        document = {"topics": topics, "config": config, "published_at": clock, "protocol": "dual-profile-v032"}
        document["version"] = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
        set_state("interests:profile", document)
        return document


def snapshot():
    return get_state("interests:profile", {"topics": [], "config": settings(), "version": "empty", "published_at": None})


def match_scores(video, profile):
    names = matched(video, profile["config"])
    topics = [t for t in profile["topics"] if t["name"] in names and t["state"] not in ("paused", "excluded")]
    return {"L": max((t["L"] for t in topics), default=0), "S": max((t["S"] for t in topics), default=0), "fixed": any(t["state"] == "fixed" for t in topics),
            "theme": max(topics, key=lambda t: (t["L"], t["S"], t["name"]))["name"] if topics else None,
            "topics": [t["name"] for t in topics if t["L"] > 0]}


def special_ups():
    with connect() as conn:
        synced = {str(row[0]): bool(row[1]) for row in conn.execute("SELECT mid,special FROM followings WHERE special IS NOT NULL")}
    for mid, value in settings()["special_ups"].items():
        if value.get("enabled") is not None:
            synced[mid] = value["enabled"]
    return {mid for mid, value in synced.items() if value}


def replay_eligible(snapshot, clock=None):
    clock = clock or time.time()
    values = {bv: pref for bv, pref in preferences().items() if pref.get("allow_replay") and bv in snapshot["favorites"]}
    with connect() as conn:
        watched = conn.execute("SELECT bvid,MAX(watched_at) at FROM history_events GROUP BY bvid").fetchall()
        actions = conn.execute("SELECT bvid,MAX(created_at) at FROM feedback WHERE action='watched' AND revoked_at IS NULL GROUP BY bvid").fetchall()
        exposed = conn.execute("SELECT r.bvid,MAX(COALESCE(e.visible_at,e.acted_at)) at FROM recommendation_history r JOIN recommendation_exposures e ON e.recommendation_id=r.id GROUP BY r.bvid").fetchall()
    latest = {}
    for row in list(watched)+list(actions)+list(exposed):
        latest[row["bvid"]] = max(latest.get(row["bvid"], 0), row["at"] or 0)
    return {bv: {"days": pref.get("replay_days", 30), "last_at": latest.get(bv) or None, "time_evidence_insufficient": not latest.get(bv)} for bv, pref in values.items() if not latest.get(bv) or clock-latest[bv] >= pref.get("replay_days", 30)*DAY}
