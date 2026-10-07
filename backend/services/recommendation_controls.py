"""即时控制、真实曝光窗口及统一作者/主体限额。"""
import hashlib
import json
import math
import time
from collections import Counter

from backend.services import entity_aliases
from backend.storage.database import connect

DEFAULTS = {"freshness": "standard", "discovery": "standard", "third_party": "small"}
FRESHNESS = {"off": 0, "weak": .04, "standard": .08, "strong": .12}
CAPS = {"off": 0, "small": 1, "standard": 2, "active": 3}


def freeze(settings):
    value = {key: settings.get(key, default) for key, default in DEFAULTS.items()}
    return {"version": "controls-v033.1", "settings": value, "dictionary": entity_aliases.snapshot(),
            "signature": hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]}


def window(conn=None, clock=None, exclude_page=None):
    clock = time.time() if clock is None else clock
    if conn is None:
        with connect() as connection:
            return window(connection, clock, exclude_page)
    rows = conn.execute("SELECT r.stream_id,r.page,r.video_snapshot,e.visible_at FROM recommendation_history r JOIN recommendation_exposures e ON e.recommendation_id=r.id WHERE r.feed_type IN ('for_you','explore') AND e.visible_at BETWEEN ? AND ? ORDER BY e.visible_at DESC,r.id DESC", (clock-1800, clock)).fetchall()
    pages = {}
    for row in rows:
        key = (row["stream_id"], row["page"])
        if key == exclude_page:
            continue
        if key not in pages and len(pages) >= 2:
            continue
        pages.setdefault(key, set()).update(entity_aliases.identities(json.loads(row["video_snapshot"] or "{}")))
    return list(pages.values())


def select(items, limit, pages=None, counts=True, exposed=()):
    frequency = Counter(key for page in (window() if pages is None else pages) for key in page)
    selected, seen, intents = [], set(), Counter()
    for item in items:
        keys = entity_aliases.identities(item)
        intent = item.get("_controls", {}).get("intent")
        options = item.get("_controls", {}).get("settings", DEFAULTS)
        if item.get("recommendation_id") not in exposed and (keys & seen or any(frequency[key] >= 2 for key in keys)):
            continue
        if counts and intent in CAPS_BY_INTENT and intents[intent] >= CAPS[options[CAPS_BY_INTENT[intent]]]:
            continue
        selected.append(item)
        seen.update(keys)
        intents[intent] += 1
        if len(selected) >= limit:
            break
    return selected


CAPS_BY_INTENT = {"discovery": "discovery", "third_party": "third_party"}


def annotate(item, policy, affinity, clock=None):
    from backend.services.interest_profile import match_scores
    clock = time.time() if clock is None else clock
    controls = policy.get("controls") or {"version": "controls-v033.1", "signature": "legacy", "settings": DEFAULTS, "dictionary": {"version": 0, "entities": []}}
    attribution = item.get("entity_attribution") or entity_aliases.identify(item["_snapshot"], controls["dictionary"])
    item["entity_attribution"] = attribution
    scores = item.get("_policy") or match_scores(item["_snapshot"], policy["profile"])
    special = set(policy["special_ups"])
    third_party = any(entity["type"] == "up" and not entity["bound"] and special & {a["mid"] for a in entity["accounts"]} for entity in attribution["entities"])
    key = str(item.get("mid") or item.get("author") or "")
    intent = "third_party" if third_party else "discovery" if key not in affinity["followings"] | special and not affinity["ups"].get(key, {}).get("regular") and (scores["L"] > 0 or scores["fixed"]) else "ordinary"
    published = item.get("pubdate")
    age = (clock-published)/86400 if isinstance(published, (int, float)) and math.isfinite(published) and 0 < published <= clock else None
    bonus = FRESHNESS[controls["settings"]["freshness"]] * 2**(-age/30) if age is not None else 0
    item["_controls"] = {"version": controls["version"], "signature": controls["signature"], "settings": controls["settings"],
                         "dictionary_version": controls["dictionary"]["version"], "intent": intent, "age_days": age, "bonus": bonus, "at": clock}
    item["freshness_score"] = (item.get("rule_score", item["rating"]) or 0)+bonus
    return item


def invalidate(conn):
    # 事务内失效，后台下轮重算；轮询本身不做评分。
    conn.execute("UPDATE app_state SET value=json_set(value,'$.status','checking') WHERE key LIKE 'feed:readiness:%'")
    conn.execute("INSERT INTO app_state VALUES('feed:revision','1',?) ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(app_state.value AS INTEGER)+1 AS TEXT),updated_at=excluded.updated_at", (time.time(),))
