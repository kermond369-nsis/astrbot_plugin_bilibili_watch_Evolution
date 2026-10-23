"""QQ 平台 B 站小程序卡片与引用消息解析引擎（Card Extractor）。

痛点解决：
群友在群里分享 B 站视频时，绝大多数情况下是通过 B 站 App 点击“分享到 QQ”，
此时发出的消息是 JSON/XML 格式的小程序卡片（或 Ark 消息），
或者通过“引用卡片 + @bot”触发。
本模块负责深度遍历消息链，提取真实的 B 站视频 URL 或短链。
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.client.bili_client import B23_RE, BV_RE

JSON_URL_KEYS = ("jumpUrl", "url", "actionData", "link", "qqdocurl", "meta")


def extract_bilibili_card_url(event: Any) -> str | None:
    """从 AstrMessageEvent 中深度提取 B 站小程序卡片里的视频链接。

    检查顺序：
    1. 当前消息链中的 Json/Xml 组件；
    2. 引用消息（Reply）中的组件；
    3. raw_message 中的原始 JSON 文本。
    """
    message_obj = getattr(event, "message_obj", None)
    if not message_obj:
        return None

    message_chain = getattr(message_obj, "message", []) or []

    # 1. 扫描当前消息链
    for comp in message_chain:
        url = _scan_component(comp)
        if url:
            return url

    # 2. 扫描引用回复链 (Reply)
    for comp in message_chain:
        chain = getattr(comp, "chain", None)
        if chain:
            for sub_comp in getattr(chain, "chain", []) or []:
                url = _scan_component(sub_comp)
                if url:
                    return url

    # 3. 扫描 raw_message 原始字符串
    raw = getattr(event, "raw_message", None)
    if isinstance(raw, dict):
        url = _search_dict_for_bili_url(raw)
        if url:
            return url
    elif isinstance(raw, str) and ("b23.tv" in raw or "bilibili.com" in raw):
        match = BV_RE.search(raw) or B23_RE.search(raw)
        if match:
            return match.group(0)

    return None


def _scan_component(comp: Any) -> str | None:
    """扫描单个 AstrBot 消息组件"""
    # 检查 Json 组件
    data = getattr(comp, "data", None)
    if data:
        if isinstance(data, dict):
            url = _search_dict_for_bili_url(data)
            if url:
                return url
        elif isinstance(data, str) and ("b23.tv" in data or "bilibili.com" in data):
            try:
                parsed = json.loads(data)
                url = _search_dict_for_bili_url(parsed)
                if url:
                    return url
            except Exception:
                pass
            match = BV_RE.search(data) or B23_RE.search(data)
            if match:
                return match.group(0)

    # 检查 Xml 或 Plain 里的文本
    text = getattr(comp, "text", "") or ""
    if "b23.tv" in text or "bilibili.com" in text:
        match = BV_RE.search(text) or B23_RE.search(text)
        if match:
            return match.group(0)

    return None


def _search_dict_for_bili_url(node: Any) -> str | None:
    """递归检索字典或列表中的 B 站链接"""
    if isinstance(node, dict):
        for k, v in node.items():
            if isinstance(v, str):
                if "b23.tv" in v or "bilibili.com" in v:
                    match = BV_RE.search(v) or B23_RE.search(v)
                    if match:
                        return match.group(0)
            elif isinstance(v, (dict, list)):
                res = _search_dict_for_bili_url(v)
                if res:
                    return res
    elif isinstance(node, list):
        for item in node:
            res = _search_dict_for_bili_url(item)
            if res:
                return res
    return None
