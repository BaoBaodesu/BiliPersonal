"""
Bilibili 接口封装。
- Cookie 只存在于本模块内存与 user_data/cookie.txt，任何返回值与日志都不包含 Cookie。
- 统一记录 -352 / -412 风控并进入退避期，退避期间不再请求候选接口。
"""

import base64
import hashlib
import json
import os
import threading
import time
import urllib.parse
from collections import OrderedDict, deque
from functools import reduce
from io import BytesIO

import qrcode
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import COOKIE_PATH, HISTORY_PATH, RATE_LIMIT_BACKOFF
from backend.services.zones import zone_of

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

QR_CODE_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_CODE_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

# WBI 签名混淆表
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]


class BiliError(Exception):
    def __init__(self, code, message=""):
        super().__init__(f"code={code} message={message}")
        self.code = code
        self.message = message


class RateLimited(BiliError):
    pass


class LoginExpired(BiliError):
    pass


def _make_session():
    session = requests.Session()
    retry = Retry(total=2, connect=2, read=2, backoff_factor=0.5,
                  status_forcelist=(500, 502, 504), raise_on_status=False)
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


class BilibiliService:
    def __init__(self):
        self._cookie = ""
        self._session = _make_session()
        self._lock = threading.Lock()
        self._last_request_at = 0.0
        self._wbi_key = None
        self._wbi_key_at = 0.0
        self._nav_cache = None
        self._nav_cache_at = 0.0
        self._detail_cache = OrderedDict()

        # 调试信息
        self.rate_limit_count = 0
        self.rate_limited_until = 0.0
        self.recent_errors = deque(maxlen=20)

        self.load_cookie()

    # ---------------- Cookie ----------------

    def load_cookie(self):
        if os.path.exists(COOKIE_PATH):
            with open(COOKIE_PATH, "r", encoding="utf-8") as f:
                self._cookie = f.read().strip()
        return bool(self._cookie)

    def save_cookie(self, cookie_str):
        os.makedirs(os.path.dirname(COOKIE_PATH), exist_ok=True)
        with open(COOKIE_PATH, "w", encoding="utf-8") as f:
            f.write(cookie_str)
        self._cookie = cookie_str
        self._nav_cache = None

    def clear_cookie(self):
        if os.path.exists(COOKIE_PATH):
            os.remove(COOKIE_PATH)
        self._cookie = ""
        self._nav_cache = None
        self._detail_cache.clear()

    @property
    def has_cookie(self):
        return bool(self._cookie)

    @property
    def rate_limited(self):
        return time.time() < self.rate_limited_until

    # ---------------- 基础请求 ----------------

    def _headers(self):
        return {"User-Agent": UA, "Referer": "https://www.bilibili.com/", "Cookie": self._cookie}

    def _record_error(self, url, code, message):
        # 只记录接口路径与错误码，不记录参数（参数中可能包含签名）
        path = urllib.parse.urlparse(url).path
        self.recent_errors.appendleft(
            {"time": time.time(), "path": path, "code": code, "message": str(message)[:120]}
        )

    def get(self, url, params=None, wbi=False, timeout=10):
        """
        GET 并返回 data 字段；业务错误抛出 BiliError / RateLimited / LoginExpired。
        简单节流：两次请求至少间隔 0.12 秒，降低触发风控的概率。
        """
        with self._lock:
            wait = 0.12 - (time.time() - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.time()
        if wbi:
            params = self._wbi_sign(params or {})
        try:
            response = self._session.get(url, headers=self._headers(), params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            self._record_error(url, "network", type(e).__name__)
            raise BiliError("network", type(e).__name__)
        if response.status_code == 412:
            self._on_rate_limited(url, 412)
        if response.status_code != 200:
            self._record_error(url, response.status_code, "HTTP error")
            raise BiliError(response.status_code, "HTTP error")
        try:
            data = response.json()
        except ValueError:
            self._record_error(url, "json", "non-json response")
            raise BiliError("json", "non-json response")
        code = data.get("code")
        if code == 0:
            return data.get("data")
        message = data.get("message", "")
        if code in (-352, -412):
            self._on_rate_limited(url, code)
        self._record_error(url, code, message)
        if code == -101:
            raise LoginExpired(code, message)
        raise BiliError(code, message)

    def _on_rate_limited(self, url, code):
        self.rate_limit_count += 1
        self.rate_limited_until = time.time() + RATE_LIMIT_BACKOFF
        self._record_error(url, code, "rate limited")
        print(f"Bilibili 风控 code={code}，{RATE_LIMIT_BACKOFF} 秒内暂停候选请求")
        raise RateLimited(code, "rate limited")

    # ---------------- WBI 签名 ----------------

    def _get_wbi_key(self):
        if self._wbi_key and time.time() - self._wbi_key_at < 3600:
            return self._wbi_key
        nav = self.nav(force=True)
        wbi_img = nav["wbi_img"]
        img_key = wbi_img["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub_key = wbi_img["sub_url"].rsplit("/", 1)[1].split(".")[0]
        raw = img_key + sub_key
        self._wbi_key = reduce(lambda s, i: s + raw[i], MIXIN_KEY_ENC_TAB, "")[:32]
        self._wbi_key_at = time.time()
        return self._wbi_key

    def _wbi_sign(self, params):
        mixin_key = self._get_wbi_key()
        params = dict(params)
        params["wts"] = round(time.time())
        params = dict(sorted(params.items()))
        params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
        query = urllib.parse.urlencode(params)
        params["w_rid"] = hashlib.md5((query + mixin_key).encode()).hexdigest()
        return params

    # ---------------- 登录 ----------------

    def qrcode_generate(self):
        response = self._session.get(QR_CODE_GENERATE_URL, headers={"User-Agent": UA}, timeout=10)
        data = response.json()
        if data.get("code") != 0:
            raise BiliError(data.get("code"), data.get("message", ""))
        url = data["data"]["url"]
        qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_L, box_size=10, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill="black", back_color="white")
        buffered = BytesIO()
        img.save(buffered, format="PNG")
        return {
            "qrcode_key": data["data"]["qrcode_key"],
            "image": "data:image/png;base64," + base64.b64encode(buffered.getvalue()).decode("utf-8"),
        }

    def qrcode_poll(self, qrcode_key):
        """
        返回 waiting / scanned / expired / success / error。
        登录成功时直接在后端保存 Cookie，不向调用方返回任何 Cookie 内容。
        """
        response = self._session.get(
            QR_CODE_POLL_URL, params={"qrcode_key": qrcode_key}, headers={"User-Agent": UA}, timeout=10
        )
        data = response.json()
        code = data.get("data", {}).get("code")
        if code == 86101:
            return "waiting"
        if code == 86090:
            return "scanned"
        if code == 86038:
            return "expired"
        if code != 0:
            return "error"
        cookie_data = response.cookies.get_dict()
        print(f"登录成功，获取到 {len(cookie_data)} 个 Cookie 字段")
        # 获取真实 buvid3/buvid4，降低候选接口触发 -352 的概率；失败时退回原版的占位值
        buvid = {"buvid3": "1"}
        try:
            spi = self._session.get(
                "https://api.bilibili.com/x/frontend/finger/spi", headers={"User-Agent": UA}, timeout=10
            ).json()
            if spi.get("code") == 0:
                buvid = {"buvid3": spi["data"]["b_3"], "buvid4": spi["data"]["b_4"]}
        except (requests.exceptions.RequestException, ValueError, KeyError):
            pass
        merged = {**buvid, **cookie_data}
        self.save_cookie("; ".join(f"{k}={v}" for k, v in merged.items()))
        return "success"

    # ---------------- 用户 ----------------

    def nav(self, force=False):
        if not force and self._nav_cache and time.time() - self._nav_cache_at < 300:
            return self._nav_cache
        data = self.get("https://api.bilibili.com/x/web-interface/nav")
        if not data.get("isLogin"):
            raise LoginExpired(-101, "not login")
        self._nav_cache = data
        self._nav_cache_at = time.time()
        return data

    def profile(self):
        nav = self.nav()
        return {
            "mid": nav["mid"],
            "name": nav["uname"],
            "face": https(nav.get("face", "")),
            "level": nav.get("level_info", {}).get("current_level"),
            "vip": bool(nav.get("vipStatus")),
        }

    # ---------------- 视频详情 ----------------

    def detail(self, bvid):
        if bvid in self._detail_cache:
            self._detail_cache.move_to_end(bvid)
            return self._detail_cache[bvid]
        data = self.get("https://api.bilibili.com/x/web-interface/view/detail", {"bvid": bvid})
        view = data["View"]
        result = {
            "bvid": bvid,
            "aid": view.get("aid"),
            "title": view.get("title", ""),
            "pic": https(view.get("pic", "")),
            "author": view["owner"]["name"],
            "mid": view["owner"]["mid"],
            "face": https(view["owner"].get("face", "")),
            "view": view["stat"]["view"],
            "like": view["stat"]["like"],
            "favorite": view["stat"]["favorite"],
            "coin": view["stat"]["coin"],
            "share": view["stat"]["share"],
            "reply": view["stat"]["reply"],
            "duration": view.get("duration", 0),
            "pubdate": view.get("pubdate", 0),
            "tname": zone_of(view.get("tid"), view.get("tname") or ""),
            "tag": [tag["tag_name"] for tag in (data.get("Tags") or [])],
        }
        self._detail_cache[bvid] = result
        if len(self._detail_cache) > 3000:
            self._detail_cache.popitem(last=False)
        return result

    # ---------------- 候选池 ----------------

    def fetch_hot(self, pn, ps=20):
        """
        热门候选：/x/web-interface/popular 分页（原版每周必看接口频繁 -352，这里改用热门列表）。
        返回 (videos, no_more)。视频结构与原版 hotVideo.json 一致，额外带 mid/tname/pubdate 等展示字段。
        """
        data = self.get("https://api.bilibili.com/x/web-interface/popular", {"pn": pn, "ps": ps})
        items = [(item["bvid"], (item.get("rcmd_reason") or {}).get("content", "")) for item in data.get("list") or []]
        return self._candidates_from_details(items, "hot"), bool(data.get("no_more"))

    def fetch_rcmd(self, fresh_idx, ps=12):
        """
        兴趣探索候选：首页推荐流（WBI 签名）。
        """
        params = {"fresh_type": 4, "ps": ps, "fresh_idx": fresh_idx, "fresh_idx_1h": fresh_idx, "brush": fresh_idx}
        data = self.get("https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd", params, wbi=True)
        items = [
            (item["bvid"], (item.get("rcmd_reason") or {}).get("content", ""))
            for item in data.get("item") or []
            if item.get("goto") == "av" and item.get("bvid")
        ]
        return self._candidates_from_details(items, "rcmd")

    def _candidates_from_details(self, items, source):
        """
        逐个补充视频详情（tag 等）。中途触发风控时保留已获取的部分，一条都没有才抛出。
        """
        videos = []
        for bvid, reason in items:
            try:
                video = dict(self.detail(bvid))
            except RateLimited:
                if videos:
                    break
                raise
            except BiliError as e:
                print(f"视频详情获取失败 {bvid}：{e}")
                continue
            video["source"] = source
            video["rcmd_reason"] = reason
            videos.append(video)
        return videos

    # ---------------- 搜索 ----------------

    def search(self, keyword, page=1):
        data = self.get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            {"search_type": "video", "keyword": keyword, "page": page},
            wbi=True,
        )
        items = []
        for r in data.get("result") or []:
            if r.get("type") != "video" or not r.get("bvid"):
                continue
            items.append({
                "bvid": r["bvid"],
                "title": strip_em(r.get("title", "")),
                "pic": https(r.get("pic", "")),
                "author": r.get("author", ""),
                "mid": r.get("mid"),
                "view": r.get("play", 0),
                "like": r.get("like", 0),
                "duration": parse_duration(r.get("duration", "")),
                "pubdate": r.get("pubdate", 0),
                "tname": zone_of(r.get("typeid"), r.get("typename", "")),
                "tag": [t for t in (r.get("tag") or "").split(",") if t],
                "source": "search",
            })
        return {"items": items, "page": data.get("page", page), "num_pages": data.get("numPages", 1)}

    # ---------------- 观看历史 / 收藏 ----------------

    def watch_history(self, max_cursor=0, view_at=0, ps=20):
        params = {"ps": ps, "type": "archive"}
        if max_cursor:
            params.update({"max": max_cursor, "view_at": view_at, "business": "archive"})
        data = self.get("https://api.bilibili.com/x/web-interface/history/cursor", params)
        items = []
        for r in data.get("list") or []:
            bvid = (r.get("history") or {}).get("bvid")
            if not bvid:
                continue
            items.append({
                "bvid": bvid,
                "title": r.get("title", ""),
                "pic": https(r.get("cover", "")),
                "author": r.get("author_name", ""),
                "mid": r.get("author_mid"),
                "duration": r.get("duration", 0),
                "progress": r.get("progress", 0),
                "view_at": r.get("view_at", 0),
                "tname": zone_of(None, r.get("tag_name", "")),
                "source": "history",
            })
        cursor = data.get("cursor") or {}
        return {"items": items, "cursor": {"max": cursor.get("max", 0), "view_at": cursor.get("view_at", 0)},
                "has_more": bool(items)}

    def fav_folders(self):
        mid = self.nav()["mid"]
        data = self.get("https://api.bilibili.com/x/v3/fav/folder/created/list-all", {"up_mid": mid})
        return [{"id": f["id"], "title": f["title"], "count": f.get("media_count", 0)} for f in (data or {}).get("list") or []]

    def fav_videos(self, media_id, pn=1, ps=20):
        data = self.get(
            "https://api.bilibili.com/x/v3/fav/resource/list",
            {"media_id": media_id, "pn": pn, "ps": ps, "order": "mtime", "platform": "web"},
        )
        items = []
        for m in data.get("medias") or []:
            if m.get("type") != 2 or not m.get("bvid"):
                continue
            items.append({
                "bvid": m["bvid"],
                "title": m.get("title", ""),
                "pic": https(m.get("cover", "")),
                "author": m.get("upper", {}).get("name", ""),
                "mid": m.get("upper", {}).get("mid"),
                "view": m.get("cnt_info", {}).get("play", 0),
                "duration": m.get("duration", 0),
                "pubdate": m.get("pubtime", 0),
                "source": "favorite",
            })
        return {"items": items, "has_more": bool(data.get("has_more"))}

    # ---------------- 训练数据（与原版 get_history_data 字段契约一致） ----------------

    def collect_training_history(self, history_len, fav_max):
        """
        与原版 getHistoryData.get_history_data 保持相同的取数方式与字段语义：
        收藏（最多 fav_max 条，progress 取 duration/2，isfaved=1，isliked=0，coin 取收藏数）
        + 观看历史（history_len 条，progress=-1 视为看完，isliked 来自 has/like）。
        额外字段 mid/tname/source 仅用于展示与兴趣画像，不进入模型。
        """
        res = []
        mid = self.nav(force=True)["mid"]

        # 收藏
        try:
            folders = self.get("https://api.bilibili.com/x/v3/fav/folder/created/list-all", {"up_mid": mid})
            folders = (folders or {}).get("list") or []
        except RateLimited:
            raise
        except BiliError as e:
            print(f"获取收藏夹列表失败：{e}")
            folders = []
        for folder in folders:
            if len(res) >= fav_max:
                break
            page_num = 1
            while len(res) < fav_max:
                try:
                    info = self.get(
                        "https://api.bilibili.com/x/v3/fav/resource/list",
                        {"media_id": folder["id"], "pn": page_num, "ps": 20, "keyword": "", "order": "mtime"},
                    )
                except RateLimited:
                    raise
                except BiliError as e:
                    print(f"获取收藏夹视频失败：{e}")
                    break
                for media in info.get("medias") or []:
                    if not media.get("bvid"):
                        continue
                    try:
                        d = self.detail(media["bvid"])
                    except RateLimited:
                        raise
                    except BiliError:
                        continue
                    res.append({
                        "bvid": media["bvid"],
                        "title": media["title"],
                        "pic": https(media["cover"]),
                        "author": media["upper"]["name"],
                        "view": media["cnt_info"]["play"],
                        "like": d["like"],
                        "favorite": d["favorite"],
                        "coin": media["cnt_info"]["collect"],
                        "share": d["share"],
                        "duration": d["duration"],
                        "progress": d["duration"] / 2,
                        "tag": d["tag"],
                        "isfaved": 1,
                        "isliked": 0,
                        "reply": d["reply"],
                        "mid": d["mid"],
                        "tname": d["tname"],
                        "source": "favorite",
                    })
                    if len(res) >= fav_max:
                        break
                if not info.get("has_more"):
                    break
                page_num += 1
        print(f"已获取{len(res)}条收藏历史记录")

        # 观看历史
        count = 0
        page = 1
        while count < history_len:
            try:
                videos_list = self.get("https://api.bilibili.com/x/v2/history", {"pn": page})
            except RateLimited:
                raise
            except BiliError as e:
                print(f"历史记录接口返回错误：{e}")
                break
            if isinstance(videos_list, dict):
                videos_list = videos_list.get("list", [])
            if not videos_list:
                break
            for video_info in videos_list:
                bvid = video_info.get("bvid")
                if not bvid:
                    continue
                try:
                    d = self.detail(bvid)
                    isliked = self.get(
                        "https://api.bilibili.com/x/web-interface/archive/has/like",
                        {"aid": video_info["stat"]["aid"]},
                    )
                except RateLimited:
                    raise
                except BiliError:
                    continue
                progress = video_info["progress"]
                if progress == -1:
                    progress = video_info["duration"]
                res.append({
                    "bvid": bvid,
                    "title": video_info["title"],
                    "pic": https(video_info["pic"]),
                    "author": video_info["owner"]["name"],
                    "view": video_info["stat"]["view"],
                    "like": video_info["stat"]["like"],
                    "favorite": video_info["stat"]["favorite"],
                    "coin": video_info["stat"]["coin"],
                    "share": video_info["stat"]["share"],
                    "duration": video_info["duration"],
                    "progress": progress,
                    "tag": d["tag"],
                    "isfaved": 1 if video_info.get("favorite") else 0,
                    "isliked": isliked,
                    "mid": video_info["owner"].get("mid"),
                    "tname": d["tname"],
                    "view_at": video_info.get("view_at", 0),
                    "source": "history",
                })
                count += 1
                if count >= history_len:
                    break
            page += 1
        print(f"已获取{count}条观看历史记录")

        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=4, ensure_ascii=False)
        return res


def https(url):
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url[len("http://"):]
    return url


def strip_em(text):
    return text.replace('<em class="keyword">', "").replace("</em>", "")


def parse_duration(text):
    """搜索接口返回 "12:34" 或 "1:02:03" 格式"""
    if isinstance(text, int):
        return text
    total = 0
    for part in str(text).split(":"):
        if part.isdigit():
            total = total * 60 + int(part)
    return total


bili = BilibiliService()
