"""Ranker 外规则策略与独立试用；不切换或重训模型。"""
import hashlib
import json
import time

from backend.services import interest_profile as profile
from backend.storage.database import connect, get_state, set_state

VERSION = "rules-v033.1"
DEFAULTS = {"interest": "standard", "exploration": "conservative", "archive": "standard"}
STRENGTH = {"off": 0, "weak": .5, "standard": 1, "strong": 1.5}
EXPLORATION = {"off": 0, "conservative": 1, "balanced": 2, "active": 3}
ARCHIVE = {"off": 0, "small": 1, "standard": 3, "enhanced": 5}


def settings():
    from backend.services.recommendation_controls import DEFAULTS as controls_defaults
    return {**DEFAULTS, **controls_defaults, **get_state("policy:settings", {})}


def signature(value, sources=None):
    from backend.services.source_mixer import settings as sources_settings
    return hashlib.sha256(json.dumps({"version": VERSION, "settings": {key: value[key] for key in DEFAULTS}, "sources": sources_settings() if sources is None else sources}, sort_keys=True).encode()).hexdigest()[:16]


def update_settings(changes):
    from backend.services.recommendation_controls import DEFAULTS as controls_defaults, FRESHNESS, CAPS
    if not isinstance(changes, dict) or set(changes)-set(DEFAULTS)-set(controls_defaults):
        raise ValueError("未知策略设置")
    if any(not isinstance(value, str) for value in changes.values()):
        raise ValueError("推荐控制档位必须是字符串")
    from backend.services.recommendation_controls import invalidate
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from backend.services.policy_review import guard_change
        guard_change(conn)
        previous = conn.execute("SELECT value FROM app_state WHERE key='policy:settings'").fetchone()
        value = {**DEFAULTS, **controls_defaults, **(json.loads(previous[0]) if previous else {}), **changes}
        if value["interest"] not in STRENGTH or value["exploration"] not in EXPLORATION or value["archive"] not in ARCHIVE:
            raise ValueError("非法策略档位")
        if value["freshness"] not in FRESHNESS or any(value[key] not in CAPS for key in ("discovery", "third_party")):
            raise ValueError("非法推荐控制档位")
        conn.execute("INSERT INTO app_state VALUES('policy:settings',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (json.dumps(value), time.time()))
        invalidate(conn)
    return value


def freeze(enabled=None):
    from backend.services.model_registry import registry
    value, trial = settings(), get_state("policy:trial", {})
    active = registry.state().get("active") or "fallback-v03"
    if enabled is None:
        enabled = trial.get("status") in ("running", "kept") and trial.get("signature") == signature(value) and trial.get("model_version") == active
        if trial.get("protocol") == "policy-blind-v2":
            from backend.services.policy_review import owner
            enabled = enabled and trial.get("owner") == owner()
    from collections import Counter
    from backend.services.recommendation_controls import window
    frequency = dict(Counter(key[4:] for page in window() for key in page if key.startswith("mid:")))
    from backend.services.recommendation_controls import freeze as freeze_controls
    from backend.services.source_mixer import settings as source_settings
    return {"version": VERSION, "signature": signature(value), "trial_id": trial.get("id") if enabled else None, "enabled": bool(enabled), "settings": value, "controls": freeze_controls(value), "sources": source_settings(),
            "profile": profile.snapshot(), "special_ups": sorted(profile.special_ups()), "frequency": frequency, "frozen_at": time.time()}


def annotate(item, policy, affinity):
    scores = profile.match_scores(item["_snapshot"], policy["profile"])
    mid = str(item.get("mid") or item.get("author") or "")
    special = mid in policy["special_ups"]
    if policy.get("controls"):
        from backend.services.entity_aliases import identify
        item["entity_attribution"] = identify(item["_snapshot"], policy["controls"]["dictionary"])
        special = special or any(entity["type"] == "up" and set(policy["special_ups"]) & {a["mid"] for a in entity["accounts"]} for entity in item["entity_attribution"]["entities"])
    explicit = scores["fixed"] or special
    exploratory = bool(item.get("stranger")) and not (scores["L"] or explicit)
    penalty = .02*min(3, policy["frequency"].get(mid, 0))
    rule_score = None if item["rating"] is None else item["rating"] + STRENGTH[policy["settings"]["interest"]]*(.12*scores["L"]+.04*scores["S"]+.04*special)-penalty
    evidence = [t for t in policy["profile"]["topics"] if t["name"] == scores["theme"]]
    item["_policy"] = {"version": policy["version"], "signature": policy["signature"], "trial_id": policy.get("trial_id"), "profile_version": policy["profile"]["version"], "settings": policy["settings"],
                       **scores, "special": special, "explicit": explicit, "exploratory": exploratory, "pure_short": bool(scores["S"] and not scores["L"] and not explicit),
                       "creator_penalty": penalty, "rule_score": rule_score, "evidence": evidence,
                       "top4_eligible": item["rating"] is not None and not item.get("downrank") and not exploratory and (explicit or scores["L"] > 0 and (mid in affinity["followings"] or affinity["ups"].get(mid, {}).get("regular")))}
    item["rule_score"] = rule_score
    return item


def trial_status():
    value = get_state("policy:trial", {"status": "off"})
    value = dict(value)
    if value.get("protocol") == "policy-blind-v2":
        from backend.services.policy_review import owner
        if value.get("owner") != owner():
            return {"status": "off"}
    if value.get("status") in ("running", "kept"):
        from backend.services.model_registry import registry
        value["requires_revalidation"] = value.get("signature") != signature(settings()) or value.get("model_version") != (registry.state().get("active") or "fallback-v03")
        if value.get("protocol") == "policy-blind-v2":
            from backend.services import entity_aliases, source_mixer, policy_review
            value["requires_revalidation"] |= value.get("settings") != settings() or value.get("sources") != source_mixer.settings() or value.get("dictionary") != entity_aliases.snapshot() or value.get("execution") != {**policy_review.EXECUTION, "ranking": VERSION}
        if value["requires_revalidation"]:
            return {**value, "historical": True, "limitations": "历史试用记录；v0.3.3规则需要重新验证"}
        value["days"] = (time.time()-value["started_at"])/86400
        with connect() as conn:
            rows = conn.execute("SELECT r.id,r.clicked,r.video_snapshot,r.rank,r.page,r.feed_type,EXISTS(SELECT 1 FROM recommendation_exposures ve WHERE ve.recommendation_id=r.id AND ve.visible_at IS NOT NULL) visible,(SELECT page_limit FROM feed_streams s WHERE s.stream_id=r.stream_id) page_limit,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='not_interested') negative,EXISTS(SELECT 1 FROM feedback f WHERE f.recommendation_id=r.id AND f.revoked_at IS NULL AND f.action='block_up') blocked,EXISTS(SELECT 1 FROM recommendation_exposures e WHERE e.recommendation_id=r.id AND (e.visible_at IS NOT NULL OR e.acted_at IS NOT NULL)) exposed FROM recommendation_history r WHERE served_at>=? AND model_version=?", (value["started_at"], value["model_version"])).fetchall()
        rows = [r for r in rows if json.loads(r["video_snapshot"] or "{}").get("_policy", {}).get("signature") == value["signature"]]
        if value.get("id"):
            rows = [{**dict(r), "exposed": r["visible"]} for r in rows if r["feed_type"] in ("for_you", "explore") and json.loads(r["video_snapshot"] or "{}").get("_policy", {}).get("trial_id") == value["id"]]
        exposed = sum(r["exposed"] for r in rows)
        from backend.services.bilibili_service import bili
        totals = get_state("requests:candidate_totals", {"attempts": 0, "search": 0})
        starting = value.get("request_start", {"attempts": 0, "search": 0})
        with connect() as conn:
            pages = conn.execute("SELECT c.items,c.page_limit,a.value context FROM feed_cache c JOIN app_state a ON a.key='stream:'||c.stream_id||':policy' WHERE c.created_at>=? AND json_extract(a.value,'$.signature')=? AND json_extract(a.value,'$.enabled')=1 AND c.feed_type='for_you'", (value["started_at"], value["signature"])).fetchall()
        if value.get("id"):
            pages = [p for p in pages if json.loads(p["context"]).get("trial_id") == value["id"]]
        value["metrics"] = {"candidate_attempts": totals["attempts"]-starting["attempts"], "search_attempts": totals["search"]-starting["search"], "rate_limited": bili.rate_limited,
                            "short_pages": sum(len(json.loads(p["items"])) < p["page_limit"] for p in pages), "pages": len(pages),
                            "long_coverage": sum(json.loads(r["video_snapshot"] or "{}").get("_policy", {}).get("L", 0) > 0 for r in rows)/len(rows) if rows else None,
                            "not_interested_rate": sum(r["negative"] and r["exposed"] for r in rows)/exposed if exposed else None,
                            "blocked_rate": sum(r["blocked"] and r["exposed"] for r in rows)/exposed if exposed else None,
                            "generated": len(rows), "exposed": exposed, "ctr_proxy": sum(bool(r["clicked"]) and bool(r["exposed"]) for r in rows)/exposed if exposed else None, "limitations": "点击是代理指标；样本量不足时不能推断真实偏好收益"}
        groups = {}
        for row in rows:
            controls = json.loads(row["video_snapshot"] or "{}").get("_controls", {})
            group = groups.setdefault(controls.get("signature", "legacy"), {"settings": controls.get("settings"), "generated": 0, "exposed": 0, "clicks": 0})
            group["generated"] += 1
            group["exposed"] += int(row["exposed"])
            group["clicks"] += int(bool(row["clicked"]) and bool(row["exposed"]))
        value["metrics"]["controls_groups"] = [{"signature": key, **group, "ctr_proxy": group["clicks"]/group["exposed"] if group["exposed"] else None} for key, group in groups.items()]
        top4 = [r for r in rows if r["page_limit"] and r["page"] is not None and 1 <= r["rank"]-r["page"]*r["page_limit"] <= 4 and r["exposed"]]
        value["metrics"].update(clicks=sum(bool(r["clicked"]) and bool(r["exposed"]) for r in rows), top4_exposed=len(top4), top4_ctr=sum(bool(r["clicked"]) for r in top4)/len(top4) if top4 else None)
    return {k: v for k, v in value.items() if k not in ("rollback", "dictionary", "request_id", "owner")}


def trial_action(action):
    from backend.services.model_registry import registry
    from backend.services.policy_review import report
    value = get_state("policy:trial", {})
    if value.get("protocol") == "policy-blind-v2" and action in ("keep", "stop"):
        from backend.services.policy_review import finish_trial
        return finish_trial(action)
    if action == "start":
        from backend.services.source_mixer import settings as sources_settings
        if sources_settings()["classic"]:
            raise ValueError("请先关闭经典首页，再开始新版策略试用")
        if registry.state().get("trial") or value.get("status") == "running" and not trial_status().get("requires_revalidation"):
            raise ValueError("策略试用与模型试用互斥，已有试用不能自动停止")
        result = report()
        if result.get("protocol") == "policy-blind-v2":
            from backend.services.policy_review import list_reviews, start_trial
            import uuid
            experiments = list_reviews()["experiments"]
            approved = next((v for v in experiments if v["status"] == "completed"), None)
            if approved:
                return start_trial(approved["id"], uuid.uuid4().hex)
        if result.get("protocol") == "policy-blind-v1" or result.get("model_version"):
            raise ValueError("旧盲评仅保留历史，请完成新版策略盲评")
        if not result.get("passed"):
            raise ValueError("需要先完成并通过五批独立策略盲评")
        if value:
            set_state(f'policy:trial:history:{value.get("started_at", time.time())}', value)
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
