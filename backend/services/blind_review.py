"""五个实际候选池的盲评工具，评分前不暴露模型来源。"""
import json
import random
import time
import uuid

import numpy as np

from backend.services.model_registry import registry, atomic_json


def capture():
    from backend.services.cache_service import pool
    from backend.services.cache_service import SOURCES
    from backend.services.recommendation_service import recommendation
    from backend.services.source_mixer import settings, mix
    state = registry.state()
    pair = state.get("evaluation")
    if not pair:
        raise ValueError("没有冻结的待评模型对")
    path = registry.root / "blind" / pair["id"]
    document = json.loads((path / "private.json").read_text(encoding="utf-8")) if (path / "private.json").exists() else {"candidate": pair["candidate"], "current": pair["current"], "batches": []}
    if len(document["batches"]) >= 5:
        raise ValueError("五批已冻结，请评分，不能替换批次")
    from backend.storage.database import connect
    with connect() as conn:
        generation = conn.execute("SELECT COALESCE(MAX(updated_at),0) FROM candidates").fetchone()[0]
    if document["batches"] and generation <= document["batches"][-1]["pool_generated_at"]:
        raise ValueError("候选池尚未更新，请在另一时间更新候选后再冻结下一批")
    # 使用本地候选快照；可在不同时间完成正常浏览/更新候选后分别冻结。
    options = settings()
    sources = tuple(source for source in SOURCES if source not in ("hot", "rcmd", "vertical_search") or options[source] != "off")
    videos = [v for source in sources for v in pool.all((source,))]
    from backend.services.recommendation_policy import freeze
    baseline_policy = freeze(False)
    top = {}
    for version in (pair["current"], pair["candidate"]):
        ranked = recommendation.rank_sources(videos, "for_you", "all", set(), registry.load(version), record_hits=False, policy=baseline_policy)
        items = mix(ranked, 10, options)
        if len(items) < 10:
            raise ValueError("某版本可评分候选不足十条，不能填造批次")
        top[version] = [v["bvid"] for v in items]
    tokens = {v["bvid"]: uuid.uuid4().hex[:12] for v in videos if any(v["bvid"] in ids for ids in top.values())}
    document["batches"].append({"frozen_at": time.time(), "pool_generated_at":generation, "positions": top, "sources_settings": options, "candidates": videos,
                                "videos": [{"id": tokens[v["bvid"]], "bvid": v["bvid"], "title": v.get("title", ""),
                                            "url": f'https://www.bilibili.com/video/{v["bvid"]}', "features": v} for v in {v["bvid"]: v for v in videos}.values() if v["bvid"] in tokens]})
    atomic_json(path / "private.json", document)
    merged = {}
    for batch in document["batches"]:
        for video in batch["videos"]:
            merged.setdefault(video["bvid"], {k: video[k] for k in ("id", "bvid", "title", "url")})
    public = list(merged.values())
    random.Random(20260930).shuffle(public)
    existing = {v["bvid"]: v.get("score") for v in json.loads((path / "rating.json").read_text(encoding="utf-8"))} if (path / "rating.json").exists() else {}
    atomic_json(path / "rating.json", [{**v, "score": existing.get(v["bvid"])} for v in public])
    return path / "rating.json"


def report():
    pair = registry.state().get("evaluation")
    if not pair:
        raise ValueError("没有待评模型对")
    path = registry.root / "blind" / pair["id"]
    document = json.loads((path / "private.json").read_text(encoding="utf-8"))
    ratings = json.loads((path / "rating.json").read_text(encoding="utf-8"))
    expected = {v["bvid"] for batch in document["batches"] for v in batch["videos"]}
    if len(document["batches"]) != 5 or len({b["frozen_at"] for b in document["batches"]}) != 5 or len(ratings) != len(expected) or {r["bvid"] for r in ratings} != expected or any(type(r.get("score")) is not int or r["score"] not in (-1,0,1,2,3) for r in ratings) or any(len(batch["positions"].get(version, [])) != 10 or len(set(batch["positions"].get(version, []))) != 10 or not set(batch["positions"].get(version, [])).issubset({v["bvid"] for v in batch["videos"]}) for batch in document["batches"] for version in (document["current"], document["candidate"])):
        raise ValueError("需要五个不同时间冻结的完整批次；全部视频评分只能为 -1/0/1/2/3")
    scores = {r["bvid"]: r["score"] for r in ratings}
    result = {"candidate": document["candidate"], "current": document["current"], "batches": 5, "complete": True, "models": {}}
    for version in (document["current"], document["candidate"]):
        batches = []
        for batch in document["batches"]:
            values = np.array([scores[b] for b in batch["positions"][version]])
            gains = np.maximum(values, 0)
            discounts = np.log2(np.arange(2, len(values)+2))
            common = np.array([max(scores[v["bvid"]], 0) for v in batch["videos"]])
            ideal = np.sum((2**np.sort(common)[::-1][:10]-1)/discounts)
            batches.append({"want_rate": float(np.mean(values >= 2)), "low_rate": float(np.mean(values == -1)),
                            "NDCG@10": float(np.sum((2**gains-1)/discounts)/ideal) if ideal else None})
        result["models"][version] = {"batches": batches, "want_rate": float(np.mean([b["want_rate"] for b in batches])),
                                     "low_rate": float(np.mean([b["low_rate"] for b in batches])),
                                     "NDCG@10":float(np.mean([b["NDCG@10"] for b in batches if b["NDCG@10"] is not None])) if any(b["NDCG@10"] is not None for b in batches) else None,
                                     "batch_variance": float(np.var([b["want_rate"] for b in batches]))}
    atomic_json(path / "report.json", result)
    return result


def approve_activate(version=None, confirmed=False):
    """首次五批多路盲评后人工准入，后续晋升仍由试用护栏处理。"""
    from backend.services.training_data import PROTOCOL
    from backend.storage.database import connect
    if not confirmed:
        raise ValueError("需要用户确认：传入 --confirm 才能启用模型")
    with registry._lock:
        state = registry.state()
        pair = state.get("evaluation")
        if not pair or state.get("trial") or state.get("anchor"):
            raise ValueError("只允许首次锚点准入；后续模型必须走在线试用护栏")
        result = report()
        version = version or pair["candidate"]
        if version != pair["candidate"] or result["candidate"] != version or result["current"] != pair["current"]:
            raise ValueError("盲评结果与待准入模型不匹配")
        document = json.loads((registry.root / "blind" / pair["id"] / "private.json").read_text(encoding="utf-8"))
        if any("sources_settings" not in batch or "candidates" not in batch for batch in document["batches"]):
            raise ValueError("需要在多路候选池上重新完成五批盲评")
        if any(batch["sources_settings"].get("classic") for batch in document["batches"]):
            raise ValueError("首次锚点准入需要混合模式的盲评")
        metadata = registry.metadata(version)
        if metadata.get("kind") != "v03" or metadata.get("protocol") != PROTOCOL:
            raise ValueError("候选不是当前协议的 v0.3 排序器")
        registry.load(version)
        registry.activate(version, anchor=True)
        registry.update(approved_protocol=PROTOCOL, evaluation=None, auto_paused=False)
        with connect() as conn:
            conn.execute("UPDATE model_runs SET status='approved' WHERE model_version=?", (version,))
        atomic_json(registry.root / "blind" / pair["id"] / "approval.json", {"version": version, "protocol": PROTOCOL, "confirmed": True, "approved_at": time.time()})
        return registry.state()
