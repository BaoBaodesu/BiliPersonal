"""策略专用 Top12 盲评，独立于模型晋升与旧 Top10 评价。"""
import json
import random
import time

from backend.services.model_registry import registry, atomic_json
from backend.services import recommendation_policy as policy


def location():
    version = registry.state().get("active") or "fallback-v03"
    return registry.root / "policy-blind" / (policy.signature(policy.settings())+"-"+version)


def capture():
    from backend.services.recommendation_service import recommendation
    from backend.services.cache_service import pool
    from backend.services.source_mixer import settings, mix
    from backend.services.affinity import affinity
    from backend.storage.database import connect
    import socket
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", 8345)) == 0:
            raise ValueError("请停止服务后捕获，保证过滤及冷却快照稳定")
    path = location()
    document = json.loads((path / "private.json").read_text(encoding="utf-8")) if (path / "private.json").exists() else {"model_version": registry.state().get("active") or "fallback-v03", "signature": policy.signature(policy.settings()), "batches": []}
    if len(document["batches"]) >= 5:
        raise ValueError("五批已冻结，不能覆盖，请评分")
    with connect() as conn:
        generation = conn.execute("SELECT COALESCE(MAX(last_refresh_at),0) FROM candidate_sources").fetchone()[0]
    if document["batches"] and generation <= document["batches"][-1]["generation"]:
        raise ValueError("候选池需在不同时间更新后再捕获下一批")
    options = settings()
    videos = pool.all(recommendation._mixed_sources("for_you", options))
    frozen = policy.freeze(True)
    positions, evidence = {}, {}
    affinity_snapshot = affinity.snapshot()
    cooled = recommendation._cooled_bvids()
    for name, enabled in (("baseline", False), ("strategy", True)):
        current = {**frozen, "enabled": enabled}
        ranked = recommendation.rank_sources(videos, "for_you", "all", set(), registry.load(document["model_version"]), snapshot=affinity_snapshot, record_hits=False, policy=current, cooled=cooled)
        items = mix(ranked, 12, {**options, "_policy": current})
        if len(items) != 12:
            raise ValueError("本地同池候选不足完整 Top12，不能填造盲评批次")
        positions[name] = [v["bvid"] for v in items]
        evidence[name] = [policy.annotate(v, frozen, affinity_snapshot)["_policy"] for v in items]
    document["batches"].append({"at": time.time(), "generation": generation, "candidates": videos, "sources": options, "policy": frozen, "cooldown": sorted(cooled), "positions": positions, "evidence": evidence})
    atomic_json(path / "private.json", document)
    existing = {v["bvid"]: v.get("score") for v in json.loads((path / "rating.json").read_text(encoding="utf-8"))} if (path / "rating.json").exists() else {}
    public = {v["bvid"]: {"bvid": v["bvid"], "title": v.get("title", ""), "url": f'https://www.bilibili.com/video/{v["bvid"]}', "score": existing.get(v["bvid"])} for batch in document["batches"] for v in batch["candidates"] if any(v["bvid"] in ids for ids in batch["positions"].values())}
    public = list(public.values())
    random.Random(20261004).shuffle(public)
    atomic_json(path / "rating.json", public)
    return str(path / "rating.json")


def report():
    path = location()
    if not (path / "private.json").exists():
        return {"passed": False, "status": "需要捕获五批策略盲评"}
    document = json.loads((path / "private.json").read_text(encoding="utf-8"))
    ratings = json.loads((path / "rating.json").read_text(encoding="utf-8"))
    expected = {bv for batch in document["batches"] for ids in batch["positions"].values() for bv in ids}
    complete = len(document["batches"]) == 5 and len({b["at"] for b in document["batches"]}) == 5 and len(ratings) == len(expected) and {r["bvid"] for r in ratings} == expected and all(type(r.get("score")) is int and r["score"] in (-1, 0, 1, 2, 3) for r in ratings)
    complete = complete and all(len(batch["positions"][name]) == 12 and len(set(batch["positions"][name])) == 12 and len(batch["evidence"][name]) == 12 for batch in document["batches"] for name in ("baseline", "strategy"))
    if not complete:
        return {"passed": False, "status": "需要五批完整 Top12 和所有 -1/0/1/2/3 人工评分", "batches": len(document["batches"])}
    scores = {v["bvid"]: v["score"] for v in ratings}
    result = {"batches": 5, "complete": True, "signature": document["signature"], "model_version": document["model_version"], "limitations": "小样本描述性护栏，不构成统计证明", "groups": {}}
    for name in ("baseline", "strategy"):
        group = {}
        for size in (12, 4):
            values = [scores[bv] for batch in document["batches"] for bv in batch["positions"][name][:size]]
            group[f"top{size}"] = {"samples": len(values), "wanted": sum(v >= 2 for v in values)/len(values), "repelled": sum(v == -1 for v in values)/len(values)}
        group["long_coverage"] = sum(e["L"] > 0 for batch in document["batches"] for e in batch["evidence"][name])/60
        result["groups"][name] = group
    base, new = result["groups"]["baseline"], result["groups"]["strategy"]
    result["passed"] = new["top12"]["wanted"] >= base["top12"]["wanted"]-.05 and new["top4"]["wanted"] >= base["top4"]["wanted"] and all(new[k]["repelled"] <= base[k]["repelled"] for k in ("top12", "top4")) and new["long_coverage"] > base["long_coverage"]
    atomic_json(path / "report.json", result)
    return result
