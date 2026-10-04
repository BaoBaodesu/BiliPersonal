"""Ranker 外规则策略与独立试用；不切换或重训模型。"""
import hashlib
import json
import time

from backend.services import interest_profile as profile
from backend.storage.database import connect, get_state, set_state

VERSION = "rules-v032.1"
DEFAULTS = {"interest": "standard", "exploration": "conservative", "archive": "standard"}
STRENGTH = {"off": 0, "weak": .5, "standard": 1, "strong": 1.5}
EXPLORATION = {"off": 0, "conservative": 1, "balanced": 2, "active": 3}
ARCHIVE = {"off": 0, "small": 1, "standard": 3, "enhanced": 5}


def settings():
    return {**DEFAULTS, **get_state("policy:settings", {})}


def signature(value):
    from backend.services.source_mixer import settings as sources_settings
    return hashlib.sha256(json.dumps({"version": VERSION, "settings": value, "sources": sources_settings()}, sort_keys=True).encode()).hexdigest()[:16]


def update_settings(changes):
    if not isinstance(changes, dict) or set(changes)-set(DEFAULTS):
        raise ValueError("未知策略设置")
    value = {**settings(), **changes}
    if value["interest"] not in STRENGTH or value["exploration"] not in EXPLORATION or value["archive"] not in ARCHIVE:
        raise ValueError("非法策略档位")
    set_state("policy:settings", value)
    return value


def freeze(enabled=None):
    from backend.services.model_registry import registry
    value, trial = settings(), get_state("policy:trial", {})
    active = registry.state().get("active") or "fallback-v03"
    if enabled is None:
        enabled = trial.get("status") in ("running", "kept") and trial.get("signature") == signature(value) and trial.get("model_version") == active
    with connect() as conn:
        views = [row[0] for row in conn.execute("SELECT r.view_id FROM recommendation_history r JOIN recommendation_exposures e ON e.recommendation_id=r.id WHERE r.view_id IS NOT NULL AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL) GROUP BY r.view_id ORDER BY MAX(COALESCE(e.visible_at,e.acted_at)) DESC LIMIT 3")]
        frequency = {}
        if views:
            for row in conn.execute(f"SELECT r.id,r.video_snapshot FROM recommendation_history r WHERE r.view_id IN ({','.join('?'*len(views))}) AND EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL))", views):
                video = json.loads(row[1] or "{}")
                mid = str(video.get("mid") or video.get("author") or "")
                frequency[mid] = frequency.get(mid, 0)+1
    return {"version": VERSION, "signature": signature(value), "enabled": bool(enabled), "settings": value,
            "profile": profile.snapshot(), "special_ups": sorted(profile.special_ups()), "frequency": frequency, "frozen_at": time.time()}


def annotate(item, policy, affinity):
    scores = profile.match_scores(item["_snapshot"], policy["profile"])
    mid = str(item.get("mid") or item.get("author") or "")
    special = mid in policy["special_ups"]
    explicit = scores["fixed"] or special
    exploratory = bool(item.get("stranger")) and not (scores["L"] or explicit)
    penalty = .02*min(3, policy["frequency"].get(mid, 0))
    rule_score = None if item["rating"] is None else item["rating"] + STRENGTH[policy["settings"]["interest"]]*(.12*scores["L"]+.04*scores["S"]+.04*special)-penalty
    evidence = [t for t in policy["profile"]["topics"] if t["name"] == scores["theme"]]
    item["_policy"] = {"version": policy["version"], "signature": policy["signature"], "profile_version": policy["profile"]["version"], "settings": policy["settings"],
                       **scores, "special": special, "explicit": explicit, "exploratory": exploratory, "pure_short": bool(scores["S"] and not scores["L"] and not explicit),
                       "creator_penalty": penalty, "rule_score": rule_score, "evidence": evidence,
                       "top4_eligible": item["rating"] is not None and not item.get("downrank") and not exploratory and (explicit or scores["L"] > 0 and (mid in affinity["followings"] or affinity["ups"].get(mid, {}).get("regular")))}
    item["rule_score"] = rule_score
    return item


def trial_status():
    value = get_state("policy:trial", {"status": "off"})
    value = dict(value)
    if value.get("status") in ("running", "kept"):
        from backend.services.model_registry import registry
        value["requires_revalidation"] = value.get("signature") != signature(settings()) or value.get("model_version") != (registry.state().get("active") or "fallback-v03")
        value["days"] = (time.time()-value["started_at"])/86400
        with connect() as conn:
            rows = conn.execute("SELECT r.id,r.clicked,r.video_snapshot,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='not_interested') negative,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='block_up') blocked,EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL)) exposed FROM recommendation_history r WHERE served_at>=? AND model_version=?", (value["started_at"], value["model_version"])).fetchall()
        rows = [r for r in rows if json.loads(r["video_snapshot"] or "{}").get("_policy", {}).get("signature") == value["signature"]]
        exposed = sum(r["exposed"] for r in rows)
        from backend.services.bilibili_service import bili
        totals = get_state("requests:candidate_totals", {"attempts": 0, "search": 0})
        starting = value.get("request_start", {"attempts": 0, "search": 0})
        with connect() as conn:
            pages = conn.execute("SELECT c.items,c.page_limit FROM feed_cache c JOIN app_state a ON a.key='stream:'||c.stream_id||':policy' WHERE c.created_at>=? AND json_extract(a.value,'$.signature')=? AND json_extract(a.value,'$.enabled')=1 AND c.feed_type='for_you'", (value["started_at"], value["signature"])).fetchall()
        value["metrics"] = {"candidate_attempts": totals["attempts"]-starting["attempts"], "search_attempts": totals["search"]-starting["search"], "rate_limited": bili.rate_limited,
                            "short_pages": sum(len(json.loads(p["items"])) < p["page_limit"] for p in pages), "pages": len(pages),
                            "long_coverage": sum(json.loads(r["video_snapshot"] or "{}").get("_policy", {}).get("L", 0) > 0 for r in rows)/len(rows) if rows else None,
                            "not_interested_rate": sum(r["negative"] and r["exposed"] for r in rows)/exposed if exposed else None,
                            "blocked_rate": sum(r["blocked"] and r["exposed"] for r in rows)/exposed if exposed else None,
                            "generated": len(rows), "exposed": exposed, "ctr_proxy": sum(bool(r["clicked"]) and bool(r["exposed"]) for r in rows)/exposed if exposed else None, "limitations": "点击是代理指标；样本量不足时不能推断真实偏好收益"}
    return value


def trial_action(action):
    from backend.services.model_registry import registry
    from backend.services.policy_review import report
    value = get_state("policy:trial", {})
    if action == "start":
        from backend.services.source_mixer import settings as sources_settings
        if sources_settings()["classic"]:
            raise ValueError("请先关闭经典首页，再开始新版策略试用")
        if registry.state().get("trial") or value.get("status") == "running":
            raise ValueError("策略试用与模型试用互斥，已有试用不能自动停止")
        result = report()
        if not result.get("passed"):
            raise ValueError("需要先完成并通过五批独立策略盲评")
        value = {"status": "running", "started_at": time.time(), "request_start": get_state("requests:candidate_totals", {"attempts": 0, "search": 0}), "signature": signature(settings()), "model_version": registry.state().get("active") or "fallback-v03"}
    elif action == "keep":
        if value.get("status") != "running" or time.time()-value["started_at"] < 7*86400 or trial_status().get("requires_revalidation"):
            raise ValueError("须试用至少七天，且模型及核心策略保持一致")
        value.update(status="kept", decided_at=time.time())
    elif action == "stop":
        value.update(status="stopped", ended_at=time.time())
    else:
        raise ValueError("操作应为 start、keep 或 stop；延长试用时保持运行即可")
    set_state("policy:trial", value)
    return trial_status()
