"""请求对象适配层：优先使用 AstrBot 新 Web API，旧版本回退 Quart。

只在插件 Web API handler 内调用；不要在模块导入期读取 request 属性。
"""

from __future__ import annotations

from typing import Any

try:  # AstrBot >= 4.20 推荐写法
    from astrbot.api.web import request as _request  # type: ignore

    HAS_ASTRBOT_WEB = True
except Exception:  # pragma: no cover - 旧运行时
    try:
        from quart import request as _request  # type: ignore

        HAS_ASTRBOT_WEB = False
    except Exception:
        _request = None
        HAS_ASTRBOT_WEB = False


def _query_proxy():
    if _request is None:
        return None
    try:
        return getattr(_request, "query", None) or getattr(_request, "args", None)
    except Exception:
        # 在插件 Web 请求上下文之外访问 proxy 会抛异常，按“无 query”处理
        return None


def query_get(name: str, default: Any = None):
    proxy = _query_proxy()
    if proxy is None:
        return default
    try:
        return proxy.get(name, default)
    except Exception:
        return default


async def json_body(default: dict | None = None) -> dict:
    default = default or {}
    if _request is None:
        return default
    try:
        if hasattr(_request, "json"):
            data = await _request.json(default=default)
            return data if isinstance(data, dict) else default
        data = await _request.get_json(silent=True)
        return data if isinstance(data, dict) else default
    except Exception:
        return default


def client_username() -> str:
    if _request is None:
        return ""
    try:
        return str(getattr(_request, "username", "") or "")
    except Exception:
        return ""
