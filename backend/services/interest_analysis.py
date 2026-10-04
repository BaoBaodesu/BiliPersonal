"""位置级主归因统计；辅助召回不重复增加曝光分母。"""
import json
import time
from collections import defaultdict

from backend.storage.database import connect, get_state
from backend.services import interest_profile as profile
from backend.services.affinity import affinity


def report(days=7):
    if days not in (7, 30):
        raise ValueError("统计窗口只能是 7 或 30 天")
    with connect() as conn:
        rows = conn.execute("SELECT r.*,EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL)) exposed,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='not_interested') negative,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='block_up') blocked FROM recommendation_history r WHERE r.served_at>=? AND r.feed_type!='following'", (time.time()-days*86400,)).fetchall()
        relationships = conn.execute("SELECT c.data,s.reasons FROM candidates c JOIN candidate_sources s USING(bvid) WHERE s.source='vertical_search'").fetchall()
    sources, queries = defaultdict(list), defaultdict(list)
    assisted = defaultdict(int)
    for row in rows:
        video = json.loads(row["video_snapshot"] or "{}")
        frozen = video.get("_policy", {})
        if row["source"]:
            sources[row["source"]].append(row)
        primary = frozen.get("primary_query")
        if primary:
            queries[primary].append(row)
        auxiliary = {reason.get("query") for reasons in frozen.get("source_reasons", {}).values() for reason in reasons if reason.get("query")}
        for query in auxiliary-{primary}:
            assisted[query] += int(row["exposed"])

    def summarize(values):
        exposed = sum(r["exposed"] for r in values)
        return {"generated": len(values), "exposed": exposed, "clicks": sum(bool(r["clicked"]) and bool(r["exposed"]) for r in values),
                "ctr_proxy": sum(bool(r["clicked"]) and bool(r["exposed"]) for r in values)/exposed if exposed else None,
                "not_interested_rate": sum(r["negative"] and r["exposed"] for r in values)/exposed if exposed else None,
                "blocked_rate": sum(r["blocked"] and r["exposed"] for r in values)/exposed if exposed else None}
    complete = defaultdict(set)
    for row in relationships:
        video = json.loads(row["data"])
        if video.get("_detail_complete"):
            for reason in json.loads(row["reasons"]):
                if reason.get("query"):
                    complete[reason["query"]].add(video["bvid"])
    states = get_state("vertical:queries", {})
    for value in states.values():
        daily = [counts for day, counts in value.get("daily", {}).items() if int(day) >= int((time.time()+8*3600)//86400)-days+1]
        if value.get("daily"):
            value.update({key: sum(v[key] for v in daily) for key in ("recalled", "deduplicated", "accepted")})
    contributions = get_state("vertical:complete", {})
    snapshot = affinity.snapshot()
    special = profile.special_ups()
    from backend.services.filter_service import filters
    from backend.services.recommendation_policy import freeze
    frequency = freeze(False)["frequency"]
    penalties = affinity.penalties()
    ups = []
    for key, up in snapshot["ups"].items():
        kept, _ = filters.filter_candidates([{"bvid": key, "mid": up["mid"], "author": up["name"]}], record=False)
        ups.append({**up, "key": key, "special": key in special, "blocked": not bool(kept), "downrank": bool(kept and kept[0].get("downrank")) or any(p["kind"] == "up" and p["key"] == key for p in penalties), "creator_penalty": .02*min(3, frequency.get(key, 0))})
    return {"days": days, "sources": [{"source": source, **summarize(values)} for source, values in sources.items()],
            "queries": [{"query": query, "state": states.get(query, {}), "complete_candidates": len(complete[query]), "complete_contributions": sum(at > time.time()-days*86400 for at in contributions.get(query, {}).values()), "assisted_exposures": assisted[query], **summarize(queries[query])} for query in sorted(set(states)|set(queries)|set(complete))],
            "ups": ups,
            "special_sync": get_state("sources:special_status", {"status": "unknown"}), "limitations": "点击率是代理指标；旧记录缺少来源时不补造主归因。辅助曝光不计入主查询分母。"}
