"""固定查询计划的实体搜索；完成页幂等，部分失败只补失败查询。"""
import json
import threading
import time
import uuid

from backend.services import entity_aliases
from backend.services.bilibili_service import bili, BiliError, LoginExpired, RateLimited
from backend.services.filter_service import filters
from backend.storage.database import connect, get_state, set_state

_lock = threading.Lock()


def clear():
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key LIKE 'search:session:%'")


def search(q, page=1, entity_id=None, cursor=None):
    with _lock:
        return _search(q, page, entity_id, cursor)


def _search(q, page, entity_id, cursor):
    with connect() as conn:
        conn.execute("DELETE FROM app_state WHERE key LIKE 'search:session:%' AND updated_at<?", (time.time()-1800,))
    if cursor:
        try:
            session_id, logical = cursor.rsplit(":", 1)
            logical = int(logical)
        except (ValueError, AttributeError):
            raise ValueError("搜索游标不合法")
        document = get_state("search:session:"+session_id)
        if not document or document["q"] != q or logical < 0 or logical > len(document["pages"]):
            raise ValueError("搜索会话已过期或游标不匹配，请重新搜索")
        if entity_id and entity_id != document["entity"]["id"] or logical == len(document["pages"]) and logical and not document["pages"][-1]["complete"]:
            raise ValueError("请先完成当前搜索页，或重新选择实体搜索")
    else:
        dictionary = entity_aliases.snapshot()
        hits = entity_aliases.matches(q, dictionary)
        choices = [entity for entity in dictionary["entities"] if entity["id"] in {hit["entity_id"] for hit in hits}]
        if not choices:
            if entity_id:
                raise ValueError("搜索词中没有所选实体")
            result = bili.search(q, int(page))
            items, _ = filters.filter_candidates(result["items"], record=True)
            return {**result, "items": list({item["bvid"]: item for item in items}.values())}
        if not entity_id and len(choices) > 1:
            return {"items": [], "entity_choices": choices, "has_more": False, "next_cursor": None, "page": 1, "num_pages": 1}
        entity = next((entity for entity in choices if entity["id"] == (entity_id or choices[0]["id"])), None)
        if entity is None:
            raise ValueError("所选实体与查询不匹配")
        hit = next(hit for hit in hits if hit["entity_id"] == entity["id"])
        canonical = q[:hit["start"]]+entity["name"]+q[hit["end"]:]
        queries = list(dict.fromkeys([q, canonical]))
        alias = next((q[:hit["start"]]+word+q[hit["end"]:] for word in entity["aliases"] if q[:hit["start"]]+word+q[hit["end"]:] not in queries), None)
        if alias:
            queries.append(alias)
        session_id, logical = uuid.uuid4().hex, 0
        document = {"q": q, "dictionary": dictionary, "entity": entity, "queries": [{"q": word, "next_page": 1, "done": False} for word in queries], "seen": [], "pages": []}
        with connect() as conn:
            conn.execute("DELETE FROM app_state WHERE key IN (SELECT key FROM app_state WHERE key LIKE 'search:session:%' ORDER BY updated_at DESC LIMIT -1 OFFSET 19)")
        set_state("search:session:"+session_id, document)
    if logical < len(document["pages"]) and document["pages"][logical].get("complete"):
        set_state("search:session:"+session_id, document)
        response = document["pages"][logical]["response"]
        items, _ = filters.filter_candidates(response["items"], record=False)
        return {**response, "items": items}
    if logical == len(document["pages"]):
        document["pages"].append({"items": [], "completed": [], "complete": False})
    current = document["pages"][logical]
    errors = []
    for index, query in enumerate(document["queries"]):
        if query["done"] or index in current["completed"]:
            continue
        try:
            result = bili.search(query["q"], query["next_page"])
        except BiliError as error:
            errors.append(error)
            if isinstance(error, (LoginExpired, RateLimited)):
                break
            continue
        current["completed"].append(index)
        query["done"] = query["next_page"] >= result.get("num_pages", query["next_page"]) and not result.get("has_more", False)
        query["next_page"] += 1
        items, _ = filters.filter_candidates(result["items"], record=True)
        for video in items:
            if video["bvid"] not in document["seen"]:
                document["seen"].append(video["bvid"])
                current["items"].append({**video, "entity_attribution": entity_aliases.identify(video, document["dictionary"])})
    current["complete"] = all(query["done"] or index in current["completed"] for index, query in enumerate(document["queries"]))
    response = {"items": current["items"], "entity": document["entity"], "queries": [query["q"] for query in document["queries"]], "dictionary_version": document["dictionary"]["version"],
                "has_more": any(not query["done"] for query in document["queries"]), "page": logical+1,
                "next_cursor": f"{session_id}:{logical+1}" if current["complete"] and any(not query["done"] for query in document["queries"]) else None,
                "retry_cursor": f"{session_id}:{logical}" if not current["complete"] else None,
                "errors": [str(error) for error in errors]}
    current["response"] = response
    set_state("search:session:"+session_id, document)
    if errors and not current["items"]:
        # 游标放入异常供首次全部失败时继续同一会话，而非重复已成功查询。
        errors[0].search_cursor = response["retry_cursor"]
        raise errors[0]
    return response
