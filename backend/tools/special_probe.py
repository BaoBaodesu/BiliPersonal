"""限速只读探针；只输出响应结构，不输出 Cookie、签名或账号信息。"""
import json
from pathlib import Path
import time

from backend.services.bilibili_service import bili, BiliError
from backend.services import request_coordination


def probe():
    result = {"at": time.time(), "logged_in": bili.has_cookie, "requests_limit": 3}
    if not bili.has_cookie:
        return {**result, "status": "no_session"}
    # 探针不进入候选任务，不更改生产候选及同步状态；沿用统一限速与风控。
    with request_coordination.scope(background=False, total=3, details=0, recalls=3, candidate=True, purpose="probe"):
        try:
            nav = bili.nav()
            data = bili.get("https://api.bilibili.com/x/relation/followings", {"vmid": nav["mid"], "pn": 1, "ps": 50})
            items = data.get("list") or []
            result.update(followings_received=len(items), special_field_count=sum("special" in v for v in items), tag_field_count=sum("tag" in v for v in items))
            if not items or any("special" not in v for v in items):
                special = bili.special_followings()
                result.update(special_endpoint="success", special_count=len(special))
            return {**result, "status": "success"}
        except BiliError as error:
            return {**result, "status": "failed", "code": error.code}


if __name__ == "__main__":
    result = probe()
    path = Path(".tmp/v032/special-probe.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
