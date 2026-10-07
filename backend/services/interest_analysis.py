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
    return {"days": days, "diagnostics": diagnostics(days), "sources": [{"source": source, **summarize(values)} for source, values in sources.items()],
            "queries": [{"query": query, "state": states.get(query, {}), "complete_candidates": len(complete[query]), "complete_contributions": sum(at > time.time()-days*86400 for at in contributions.get(query, {}).values()), "assisted_exposures": assisted[query], **summarize(queries[query])} for query in sorted(set(states)|set(queries)|set(complete))],
            "ups": ups,
            "special_sync": get_state("sources:special_status", {"status": "unknown"}), "limitations": "点击率是代理指标；旧记录缺少来源时不补造主归因。辅助曝光不计入主查询分母。"}


def diagnostics(days):
    """按位置级真实可见统计，不用当前词典改写过去归因。"""
    with connect() as conn:
        generated = conn.execute("SELECT * FROM recommendation_history WHERE served_at>=? AND feed_type IN ('for_you','explore')", (time.time()-days*86400,)).fetchall()
        exposed = conn.execute("SELECT r.*,e.visible_at FROM recommendation_history r JOIN recommendation_exposures e ON e.recommendation_id=r.id WHERE e.visible_at BETWEEN ? AND ? AND r.feed_type IN ('for_you','explore') ORDER BY e.visible_at,r.id", (time.time()-days*86400, time.time())).fetchall()
        readiness = [json.loads(row[0]) for row in conn.execute("SELECT value FROM app_state WHERE key LIKE 'feed:readiness:%'")]
    freshness = {name: {"generated": 0, "exposed": 0} for name in ("7天内", "8–30天", "31–90天", "91–365天", "365天以上", "未知日期")}
    aliases = {name: {"generated": 0, "exposed": 0} for name in ("account", "title", "tag")}
    for kind, rows in (("generated", generated), ("exposed", exposed)):
        for row in rows:
            video = json.loads(row["video_snapshot"] or "{}")
            age = video.get("_controls", {}).get("age_days")
            if age is None and 0 < (video.get("pubdate") or 0) <= row["served_at"]:
                age = (row["served_at"]-video["pubdate"])/86400
            bucket = "未知日期" if age is None else "7天内" if age <= 7 else "8–30天" if age <= 30 else "31–90天" if age <= 90 else "91–365天" if age <= 365 else "365天以上"
            freshness[bucket][kind] += 1
            for field in {hit["field"] for entity in video.get("entity_attribution", {}).get("entities", []) for hit in entity["hits"]}:
                if field in aliases:
                    aliases[field][kind] += 1
    repeated = {name: {"same_page": 0, "cross_page": 0, "attributed": 0} for name in ("publisher", "subject")}
    seen, active = {}, {}
    for row in exposed:
        video = json.loads(row["video_snapshot"] or "{}")
        page = (row["stream_id"], row["page"])
        keys = {"publisher": {str(video.get("mid") or video.get("author"))} if video.get("mid") or video.get("author") else set(),
                "subject": {entity["entity_id"] for entity in video.get("entity_attribution", {}).get("entities", []) if entity["type"] == "up"}}
        active = {key: [(at, value) for at, value in items if at >= row["visible_at"]-1800] for key, items in active.items()}
        previous = sorted((key for key, items in active.items() if items and key != page), key=lambda key: active[key][-1][0], reverse=True)[:2]
        for name, values in keys.items():
            if not values:
                continue
            repeated[name]["attributed"] += 1
            repeated[name]["same_page"] += bool(values & seen.setdefault(page, {"publisher": set(), "subject": set()})[name])
            repeated[name]["cross_page"] += any(values & evidence[name] for key in previous for _, evidence in active[key])
            seen[page][name].update(values)
        active.setdefault(page, []).append((row["visible_at"], keys))
    return {"generated": len(generated), "exposed": len(exposed), "repetition": {name: {**counts, "same_page_rate": counts["same_page"]/counts["attributed"] if counts["attributed"] else None, "cross_page_rate": counts["cross_page"]/counts["attributed"] if counts["attributed"] else None} for name, counts in repeated.items()},
            "freshness": [{"name": name, **counts} for name, counts in freshness.items()], "aliases": [{"field": name, **counts} for name, counts in aliases.items()], "readiness": readiness,
            "limitations": "真实曝光仅使用visible_at；跨页重复指最近30分钟前两个曝光页出现过，不等同超限。库存是当前检查估计，不预留。"}
