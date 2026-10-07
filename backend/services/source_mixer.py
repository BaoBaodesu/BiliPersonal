"""固定配额混合与来源设置；不把来源写入排序器特征。"""
from collections import Counter

from backend.config import SOURCE_DEFAULTS, SOURCE_LEVELS, SOURCE_WEIGHTS, SOURCE_UP_LIMIT, SOURCE_STRANGER_LIMIT
from backend.services.affinity import up_key
from backend.storage.database import get_state, set_state, connect
from backend.services import feed_performance as perf


def settings():
    return {**SOURCE_DEFAULTS, **get_state("sources:settings", {})}


def update_settings(changes):
    if not isinstance(changes, dict) or set(changes) - set(SOURCE_DEFAULTS):
        raise ValueError("未知来源设置")
    if any(changes.get(s, "off") not in SOURCE_LEVELS for s in ("hot", "rcmd", "vertical_search")) or ("classic" in changes and type(changes["classic"]) is not bool):
        raise ValueError("非法来源档位或经典首页开关")
    from backend.services.recommendation_controls import invalidate
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from backend.services.policy_review import guard_change, _state, _write_state
        guard_change(conn)
        value = {**SOURCE_DEFAULTS, **_state(conn, "sources:settings", {}), **changes}
        _write_state(conn, "sources:settings", value)
        invalidate(conn)
    return value


def quotas(limit, options):
    from backend.services.recommendation_policy import ARCHIVE
    policy = options.get("_policy", {})
    weights = dict(SOURCE_WEIGHTS)
    if policy.get("enabled"):
        weights["up_archive"] = ARCHIVE[policy["settings"]["archive"]]
    counts = {}
    for source in ("rcmd", "hot", "vertical_search"):
        counts[source] = min(limit-sum(counts.values()), {"small": 1, "standard": 3}.get(options.get(source, "off"), 0))
    remaining = limit-sum(counts.values())
    total = sum(weights.values())
    counts.update({s: remaining*w//total for s, w in weights.items()})
    for source in sorted(weights, key=lambda s: -(remaining*weights[s]%total))[:remaining-sum(counts[s] for s in weights)]:
        counts[source] += 1
    return counts


@perf.measured("mixer_ms")
def mix(pools, limit=12, options=None, following=False):
    options = options or settings()
    queues = {s: list(v) for s, v in pools.items()}
    from backend.services.recommendation_policy import EXPLORATION, ARCHIVE
    policy = options.get("_policy", {})
    enabled = policy.get("enabled", False) and not following
    if not following:
        for source in ("hot", "rcmd", "vertical_search"):
            if options.get(source, "off") == "off":
                queues[source] = [v for v in queues.get(source, []) if source == "vertical_search" and v.get("_controls", {}).get("intent") == "third_party"]
        if enabled and ARCHIVE[policy["settings"]["archive"]] == 0:
            queues["up_archive"] = []
    targets = quotas(limit, options) if not following else {"follow": limit}
    selected, downranked, seen = [], [], set()
    ups, strangers = Counter(), Counter()

    def take(source, wanted=None):
        while queues.get(source):
            if wanted is not None:
                index = next((i for i, v in enumerate(queues[source]) if v["bvid"] == wanted), None)
                if index is None:
                    return False
                video = queues[source].pop(index)
            else:
                video = queues[source].pop(0)
            key = up_key(video)
            intent = video.get("_controls", {}).get("intent")
            stranger = source == "related" and video.get("stranger") and intent not in ("discovery", "third_party")
            if video["bvid"] in seen or (not following and ups[key] >= 1):
                continue
            if not following and video.get("_controls"):
                from backend.services.recommendation_controls import select
                if len(select(selected+downranked+[video], len(selected+downranked)+1, options.get("_exposure_pages"))) != len(selected+downranked)+1:
                    continue
            if enabled and source in ("hot", "rcmd", "vertical_search") and intent != "third_party" and options.get(source) != "fallback" and sum(v["source"] == source and v.get("_controls", {}).get("intent") != "third_party" for v in selected+downranked) >= targets.get(source, 0):
                continue
            meta = video.get("_policy", {})
            chosen = selected+downranked
            if video.get("replay") and any(v.get("replay") for v in chosen):
                continue
            if enabled and ((meta.get("pure_short") and sum(v.get("_policy", {}).get("pure_short", False) for v in chosen) >= 3) or
                            (meta.get("exploratory") and intent not in ("discovery", "third_party") and sum(v.get("_policy", {}).get("exploratory", False) and v.get("_controls", {}).get("intent") not in ("discovery", "third_party") for v in chosen) >= EXPLORATION[policy["settings"]["exploration"]])):
                continue
            if stranger and (sum(strangers.values()) >= SOURCE_STRANGER_LIMIT or strangers[key]):
                continue
            if video.get("downrank") and downranked:
                continue
            seen.add(video["bvid"])
            ups[key] += 1
            if stranger:
                strangers[key] += 1
            (downranked if video.get("downrank") else selected).append(video)
            return True
        return False

    if enabled:
        covered = set()
        available = [(source, v) for source, videos in queues.items() if source not in ("hot", "rcmd", "vertical_search") or options.get(source) != "fallback" for v in videos if v.get("_policy", {}).get("L", 0) > 0 and not v.get("downrank")]
        available.sort(key=lambda entry: (entry[1]["rating"] is None, -(entry[1].get("rule_score") or 0)))
        while available and len(selected) < min(4, limit):
            index = next((i for i, (_, v) in enumerate(available) if set(v["_policy"]["topics"])-covered), 0)
            source, video = available.pop(index)
            if take(source, video["bvid"]):
                covered.update(video["_policy"]["topics"])
    # 个性化来源交错输出，原生来源的固定名额先保留。
    remaining_targets = {s: count-sum(v["source"] == s for v in selected+downranked) for s, count in targets.items()}
    if not following and any(v.get("_controls", {}).get("intent") == "third_party" for v in queues.get("vertical_search", [])):
        from backend.services.recommendation_controls import CAPS
        remaining_targets["vertical_search"] = max(remaining_targets.get("vertical_search", 0), CAPS[policy.get("controls", {}).get("settings", {}).get("third_party", "small")])
    for index in range(max(remaining_targets.values(), default=0)):
        for source in ("follow", "related", "up_archive", "rcmd", "hot", "vertical_search"):
            if len(selected)+len(downranked) < limit and index < remaining_targets.get(source, 0):
                take(source)
    while len(selected) + len(downranked) < limit:
        if not any(take(source) for source in ("follow", "related", "up_archive") if queues.get(source)):
            break
    for source in ("rcmd", "hot", "vertical_search"):
        if options.get(source, "off") == "fallback":
            while len(selected) + len(downranked) < limit and take(source):
                pass
    # 定向第三方不占主题查询份额，也不预留位置，只补合格空位。
    if not following:
        queues["vertical_search"] = [v for v in pools.get("vertical_search", []) if v.get("_controls", {}).get("intent") == "third_party"]
        while len(selected)+len(downranked) < limit and take("vertical_search"):
            pass
    if enabled:
        eligible = [v for v in selected if v.get("_policy", {}).get("top4_eligible")]
        selected = eligible[:4]+[v for v in selected if v not in eligible[:4]]
        for video in selected+downranked:
            video["policy_diagnostics"] = {"top4_gap": max(0, 4-len(eligible)), "long_gap": max(0, 4-sum(v.get("_policy", {}).get("L", 0) > 0 for v in selected)), "short_page": len(selected)+len(downranked) < limit}
    return (selected + downranked)[:limit]


def source_report():
    import time
    with connect() as conn:
        rows = conn.execute("SELECT r.source,COUNT(*) served,SUM(EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) exposed,SUM(r.clicked AND EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) clicked,SUM(EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.action='not_interested' AND f.revoked_at IS NULL) AND EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) negative FROM recommendation_history r WHERE r.feed_type='for_you' AND r.source IN ('follow','related','up_archive','rcmd','hot','vertical_search') AND r.served_at>=? GROUP BY r.source", (time.time() - 7 * 86400,)).fetchall()
    total = sum(row["served"] for row in rows)
    from backend.services.interest_analysis import report
    details = report(7)
    blocked = {row["source"]: row["blocked_rate"] for row in details["sources"]}
    return {"details": details, "days": 7, "total": total, "sources": [{"source": row["source"] or "legacy", "served": row["served"], "exposed": row["exposed"], "blocked_rate": blocked.get(row["source"]),
            "share": row["served"] / total if total else 0, "click_rate": row["clicked"] / row["exposed"] if row["exposed"] else None,
            "not_interested_rate": row["negative"] / row["exposed"] if row["exposed"] else None} for row in rows]}
