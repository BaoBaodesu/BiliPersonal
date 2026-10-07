"""人工实体词典与保守归因；不把召回查询当作实体证据。"""
import json
import re
import time

from backend.storage.database import connect, get_state


def snapshot():
    return get_state("entities:dictionary", {"version": 0, "entities": []})


def update(value):
    if not isinstance(value, dict) or not isinstance(value.get("entities"), list) or len(value["entities"]) > 200:
        raise ValueError("实体词典格式不合法，最多维护200个实体")
    entities, ids, mids = [], set(), set()
    for item in value["entities"]:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not re.fullmatch(r"[\w-]{1,64}", item["id"]):
            raise ValueError("实体需要稳定且唯一的ID")
        if item["id"] in ids or item.get("type") not in ("up", "character", "work") or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("实体ID、类型或名称不合法")
        if not isinstance(item.get("aliases", []), list) or not isinstance(item.get("accounts", []), list):
            raise ValueError("别名和绑定账号必须是列表")
        if any(not isinstance(word, str) or not 1 <= len(word.strip()) <= 100 for word in [item["name"], *item.get("aliases", [])]):
            raise ValueError("名称和别名应为1至100个字符")
        accounts = []
        for account in item.get("accounts", []):
            if not isinstance(account, dict) or not str(account.get("mid", "")).isdigit() or int(account["mid"]) <= 0 or item["type"] != "up":
                raise ValueError("只有UP实体可以绑定有效MID")
            if str(int(account["mid"])) in mids:
                raise ValueError("同一MID只能绑定一个UP实体")
            mids.add(str(int(account["mid"])))
            accounts.append({"mid": str(int(account["mid"])), "name": str(account.get("name", ""))[:100]})
        ids.add(item["id"])
        entities.append({"id": item["id"], "type": item["type"], "name": item["name"].strip(),
                         "aliases": list(dict.fromkeys(word.strip() for word in item.get("aliases", []) if word.strip() != item["name"].strip())), "accounts": accounts})
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        from backend.services.policy_review import guard_change
        guard_change(conn)
        previous = conn.execute("SELECT value FROM app_state WHERE key='entities:dictionary'").fetchone()
        previous = json.loads(previous[0]) if previous else {"version": 0}
        if value.get("version", previous["version"]) != previous["version"]:
            raise ValueError("词典已被其他页面修改，请重新加载后保存")
        value = {"version": previous["version"]+1, "entities": entities}
        conn.execute("INSERT INTO app_state VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                     ("entities:dictionary", json.dumps(value, ensure_ascii=False), time.time()))
        from backend.services.recommendation_controls import invalidate
        invalidate(conn)
    return value


def matches(text, dictionary):
    text = str(text or "")
    hits = []
    for entity in dictionary["entities"]:
        for word in [entity["name"], *entity["aliases"]]:
            for match in re.finditer(re.escape(word), text, re.IGNORECASE):
                before, after = text[:match.start()][-1:], text[match.end():][:1]
                if re.match(r"[A-Za-z0-9]", word[0]) and re.match(r"[A-Za-z0-9]", before) or re.match(r"[A-Za-z0-9]", word[-1]) and re.match(r"[A-Za-z0-9]", after):
                    continue
                if re.fullmatch(r"[\u4e00-\u9fff]{1,2}", word) and (before and before.isalnum() or after and after.isalnum()):
                    continue
                hits.append({"entity_id": entity["id"], "word": word, "start": match.start(), "end": match.end()})
    # 相同跨度的一词多义保留；较短且重叠的名称不参与归因。
    return [hit for hit in hits if not any(other["start"] <= hit["start"] and other["end"] >= hit["end"] and other["end"]-other["start"] > hit["end"]-hit["start"] for other in hits)]


def identify(video, dictionary=None):
    dictionary = dictionary or snapshot()
    evidence = []
    names = {}
    for entity in dictionary["entities"]:
        for word in [entity["name"], *entity["aliases"]]:
            names.setdefault(word.casefold(), set()).add(entity["id"])
    hits = matches(video.get("title", ""), dictionary)
    for entity in dictionary["entities"]:
        bound = str(video.get("mid", "")) in {a["mid"] for a in entity["accounts"]}
        exact_tags = [word for word in [entity["name"], *entity["aliases"]] if word.casefold() in {str(tag).casefold() for tag in video.get("tag", video.get("tags", []))}]
        title_hits = [hit for hit in hits if hit["entity_id"] == entity["id"]]
        unique = [hit for hit in title_hits if len(names[hit["word"].casefold()]) == 1]
        clear_tags = [word for word in exact_tags if len(names[word.casefold()]) == 1]
        if bound or unique or clear_tags:
            evidence.append({"entity_id": entity["id"], "type": entity["type"], "name": entity["name"],
                             "accounts": entity["accounts"], "bound": bound,
                             "hits": ([{"field": "account", "text": str(video["mid"])}] if bound else []) +
                                     [{"field": "title", "text": hit["word"]} for hit in unique] + [{"field": "tag", "text": word} for word in clear_tags]})
    return {"version": dictionary["version"], "entities": evidence}


def identities(video):
    keys = {"mid:"+str(video.get("mid") or video.get("author") or "")} - {"mid:"}
    for entity in video.get("entity_attribution", {}).get("entities", []):
        if entity["type"] == "up":
            keys.add("entity:"+entity["entity_id"])
            keys.update("mid:"+str(account["mid"]) for account in entity["accounts"])
    return keys
