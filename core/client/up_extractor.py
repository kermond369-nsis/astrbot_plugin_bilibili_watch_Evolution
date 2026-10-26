"""UP 主主页信息与近期投稿查询引擎（UP Extractor）。

优先采用游客高容忍的 card 接口，降低空间接口触发 412/352 风控几率。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from core.client.bili_client import BiliClient

UID_QUERY_RE = re.compile(r"(?i)^\s*(?:uid)?\s*[:：]?\s*(\d+)\s*$")
SPACE_RE = re.compile(r"space\.bilibili\.com/(\d+)")


@dataclass
class UpProfile:
    uid: int
    name: str
    sign: str
    level: int
    fans: int
    archive_count: int = 0
    recent_archives: list[str] = None

    def __post_init__(self):
        if self.recent_archives is None:
            self.recent_archives = []


def normalize_up_query(query: str) -> str:
    """宽容解析用户输入的 UP 主查询：uid:123 / uid 123 / 123 / 空间链接。"""
    text = query.strip()
    match_space = SPACE_RE.search(text)
    if match_space:
        return match_space.group(1)
    match_uid = UID_QUERY_RE.match(text)
    if match_uid:
        return match_uid.group(1)
    return text


def fetch_up_profile(client: BiliClient, uid: int) -> UpProfile:
    """获取 UP 主主页信息与近 5 条近期投稿"""
    client.ensure_buvid()

    # 1. 优先使用 card 接口 (稳定性最好，极少风控)
    url_card = f"https://api.bilibili.com/x/web-interface/card?mid={uid}"
    payload = client.get_json(url_card)
    data = payload.get("data") or {}
    card = data.get("card") or {}

    name = str(card.get("name") or "")
    sign = str(card.get("sign") or "")
    level = int(((card.get("level_info") or {}).get("current_level")) or 0)
    fans = int(card.get("fans") or data.get("follower") or 0)
    archive_count = int(data.get("archive_count") or 0)

    # 2. 获取近期投稿
    archives: list[str] = []
    try:
        # WBI 空间投稿接口
        params = client.wbi_sign({
            "mid": uid,
            "ps": 5,
            "pn": 1,
            "order": "pubdate",
        })
        url_arc = f"https://api.bilibili.com/x/space/wbi/arc/search?{params}"
        arc_payload = client.get_json(url_arc, headers={"Referer": f"https://space.bilibili.com/{uid}"})
        arc_data = arc_payload.get("data") or {}
        vlist = (arc_data.get("list") or {}).get("vlist") or []
        for item in vlist[:5]:
            t = str(item.get("title") or "").strip()
            if t:
                archives.append(t[:80])
        if not archive_count:
            archive_count = int((arc_data.get("page") or {}).get("count") or 0)
    except Exception as exc:
        logger.warning(f"获取 UP 投稿失败 uid={uid}: {exc}")

    if not name:
        raise ValueError(f"未找到 UID {uid} 的公开信息或接口受限")

    return UpProfile(
        uid=uid,
        name=name,
        sign=sign,
        level=level,
        fans=fans,
        archive_count=archive_count,
        recent_archives=archives,
    )
