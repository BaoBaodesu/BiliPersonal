"""仅后台执行的主题查询，自动查询不构成用户行为证据。"""
import time

from backend.services import interest_profile as profile, request_coordination as requests_scope
from backend.services.bilibili_service import bili, BiliError, LoginExpired
from backend.services.filter_service import filters
from backend.storage.database import get_state, set_state


def directions(snapshot):
    config = snapshot["config"]
    long, short = [], []
    for topic in snapshot["topics"]:
        if topic["state"] in ("paused", "excluded"):
            continue
        words = config["themes"].get(topic["name"], {}).get("queries", []) or [topic["name"]]
        if topic["L"] or topic["state"] == "fixed":
            long += [{"query": word, "theme": topic["name"], "explicit": topic["state"] == "fixed"} for word in words]
        elif topic["S"]:
            short += [{"query": word, "theme": topic["name"], "score": topic["S"]} for word in words]
    for mid, up in config["special_ups"].items():
        if mid in profile.special_ups():
            long += [{"query": word, "special_mid": mid, "explicit": True} for word in up.get("queries", [])]
    long += [{"query": word, "theme": value.get("theme"), "explicit": True} for word, value in config["queries"].items() if not value.get("paused") and value.get("theme") in config["themes"] and config["themes"][value["theme"]].get("state") not in ("paused", "excluded")]
    denied = set(config["query_blacklist"])
    seen = set()
    def valid(items):
        result = []
        for item in items:
            query = item["query"].strip()
            if not query or query in seen or query in denied or config["queries"].get(query, {}).get("paused"):
                continue
            seen.add(query)
            result.append({**item, "query": query})
        return result
    long = valid(long)
    offset = get_state("vertical:rotation", 0) % max(1, len(long))
    long = long[offset:]+long[:offset]
    return long[:6]+valid(sorted(short, key=lambda item: -item["score"]))[:2]


def fetch(stop=None):
    from backend.services.source_mixer import settings
    if settings().get("vertical_search", "off") == "off":
        return []
    state = get_state("vertical:queries", {})
    for direction in directions(profile.snapshot()):
        query = direction["query"]
        value = state.get(query, {})
        if value.get("failed_at", 0) > time.time()-900:
            # 不在同一失败轮次批量更换查询。
            return []
        if value.get("hour_at", 0) <= time.time()-3600:
            value.update(hour_at=time.time(), pages=0, seen=[])
        if value.get("pages", 0) >= 2:
            continue
        if stop and stop.is_set():
            return []
        value["pages"] = value.get("pages", 0)+1
        value["query"] = query
        value["theme"] = direction.get("theme")
        value["special_mid"] = direction.get("special_mid")
        state[query] = value
        set_state("vertical:queries", state)
        try:
            with requests_scope.scope(purpose="search"):
                result = bili.search(query, value["pages"])
        except LoginExpired:
            raise
        except BiliError as error:
            value.update(failed_at=time.time(), error_code=error.code)
            set_state("vertical:queries", state)
            return []
        if stop and stop.is_set():
            return []
        seen = set(value.get("seen", []))
        before_seen = len(seen)
        videos = []
        for video in result["items"]:
            if video["bvid"] not in seen:
                seen.add(video["bvid"])
                videos.append({**video, "source": "vertical_search", "query": query, "query_theme": direction.get("theme"), "special_mid": direction.get("special_mid"), "_detail_complete": False})
        videos, _ = filters.filter_candidates(videos, record=False)
        unique_count = len(seen)-before_seen
        value["seen"] = sorted(seen)
        day = str(int((time.time()+8*3600)//86400))
        daily = {k: v for k, v in value.get("daily", {}).items() if int(k) >= int(day)-30}
        counts = daily.setdefault(day, {"recalled": 0, "deduplicated": 0, "accepted": 0})
        counts["recalled"] += len(result["items"])
        counts["deduplicated"] += len(result["items"])-unique_count
        counts["accepted"] += len(videos)
        value["daily"] = daily
        value.update(recalled=value.get("recalled", 0)+len(result["items"]), deduplicated=value.get("deduplicated", 0)+len(result["items"])-unique_count,
                     accepted=value.get("accepted", 0)+len(videos), last_at=time.time(), error_code=None)
        set_state("vertical:queries", state)
        set_state("vertical:rotation", get_state("vertical:rotation", 0)+1)
        return videos
    return []


def eligible_reasons(reasons, config, special):
    allowed = []
    for reason in reasons:
        query = reason.get("query", "")
        theme = reason.get("query_theme") or config["aliases"].get(profile.normalize(query), profile.normalize(query))
        if not query or query in config["query_blacklist"] or config["queries"].get(query, {}).get("paused") or config["themes"].get(theme, {}).get("state") in ("paused", "excluded"):
            continue
        if reason.get("special_mid") and str(reason["special_mid"]) not in special:
            continue
        allowed.append(reason)
    return allowed
