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
    if any(changes.get(s, "off") not in SOURCE_LEVELS for s in ("hot", "rcmd")) or ("classic" in changes and type(changes["classic"]) is not bool):
        raise ValueError("非法来源档位或经典首页开关")
    value = {**settings(), **changes}
    set_state("sources:settings", value)
    return value


def quotas(limit, options):
    counts = {s: min(limit, {"small": 1, "standard": 3}.get(options[s], 0)) for s in ("rcmd", "hot")}
    # 小页也先给原生来源占位，不超出页面容量。
    counts["hot"] = min(counts["hot"], limit - counts["rcmd"])
    remaining = limit - sum(counts.values())
    total = sum(SOURCE_WEIGHTS.values())
    counts.update({s: remaining * w // total for s, w in SOURCE_WEIGHTS.items()})
    for source in sorted(SOURCE_WEIGHTS, key=lambda s: -(remaining * SOURCE_WEIGHTS[s] % total))[:remaining - sum(counts[s] for s in SOURCE_WEIGHTS)]:
        counts[source] += 1
    return counts


@perf.measured("mixer_ms")
def mix(pools, limit=12, options=None, following=False):
    options = options or settings()
    queues = {s: list(v) for s, v in pools.items()}
    targets = quotas(limit, options) if not following else {"follow": limit}
    selected, downranked, seen = [], [], set()
    ups, strangers = Counter(), Counter()

    def take(source):
        while queues.get(source):
            video = queues[source].pop(0)
            key = up_key(video)
            stranger = source == "related" and video.get("stranger")
            if video["bvid"] in seen or (not following and ups[key] >= SOURCE_UP_LIMIT):
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

    # 个性化来源交错输出，原生来源的固定名额先保留。
    for index in range(max(targets.values(), default=0)):
        for source in ("follow", "related", "up_archive", "rcmd", "hot"):
            if index < targets.get(source, 0):
                take(source)
    while len(selected) + len(downranked) < limit:
        if not any(take(source) for source in ("follow", "related", "up_archive") if queues.get(source)):
            break
    for source in ("rcmd", "hot"):
        if options[source] == "fallback":
            while len(selected) + len(downranked) < limit and take(source):
                pass
    return (selected + downranked)[:limit]


def source_report():
    import time
    with connect() as conn:
        rows = conn.execute("SELECT r.source,COUNT(*) served,SUM(EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) exposed,SUM(r.clicked AND EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) clicked,SUM(EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.action='not_interested' AND f.revoked_at IS NULL) AND EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))) negative FROM recommendation_history r WHERE r.feed_type='for_you' AND r.source IN ('follow','related','up_archive','rcmd','hot') AND r.served_at>=? GROUP BY r.source", (time.time() - 7 * 86400,)).fetchall()
    total = sum(row["served"] for row in rows)
    return {"days": 7, "total": total, "sources": [{"source": row["source"] or "legacy", "served": row["served"], "exposed": row["exposed"],
            "share": row["served"] / total if total else 0, "click_rate": row["clicked"] / row["exposed"] if row["exposed"] else None,
            "not_interested_rate": row["negative"] / row["exposed"] if row["exposed"] else None} for row in rows]}
