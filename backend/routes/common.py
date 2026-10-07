"""
路由公共部分：统一错误格式。
401 login_expired / 429 rate_limited / 502 bilibili_error / 400 bad_request
"""

from functools import wraps

from flask import jsonify

from backend.services.bilibili_service import BiliError, LoginExpired, RateLimited, bili
from backend.services.policy_review import ReviewError


def error(code, message, status):
    return jsonify({"error": code, "message": message}), status


def api(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ReviewError as e:
            return error(e.code, str(e), e.status)
        except LoginExpired:
            return error("login_expired", "登录已失效，请重新扫码登录", 401)
        except RateLimited:
            return error("rate_limited", "Bilibili 暂时限制了请求，请稍后再试", 429)
        except BiliError as e:
            return error("bilibili_error", f"Bilibili 接口错误 {e.code}", 502)
        except (ValueError, KeyError) as e:
            return error("bad_request", str(e), 400)

    return wrapper


def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not bili.has_cookie:
            return error("not_logged_in", "未登录", 401)
        return fn(*args, **kwargs)

    return wrapper
