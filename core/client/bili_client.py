"""B 站底层数据交互客户端（BiliClient）。

自研零重量级依赖实现：
1. 强制走 IPv4 连接（重写 HTTPSConnection），根治无 IPv6 机器超时 40~80s 痛点；
2. 动态 WBI 签名与 nav 密钥 30s 轮转刷新；
3. 游客 buvid3/buvid4 指纹生成与风控规避（dm_params）；
4. 412/429/-352 状态码指数退避重试；
5. 提供视频元数据、CC/AI字幕、XML弹幕、评论区、Dash音视频流地址的高速获取。
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import http.client
import json
import math
import random
import re
import socket
import ssl
import string
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from core.models import RawComment, RawDanmakuItem, RawSubtitleItem, RawVideoMeta

MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61,
    26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36,
    20, 34, 44, 52,
]

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
AV_RE = re.compile(r"av(\d+)", re.IGNORECASE)
B23_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+")


class _IPv4HTTPSConnection(http.client.HTTPSConnection):
    """仅走 IPv4 的 HTTPS 连接，彻底杜绝无 IPv6 出口时的多重重试假死。"""

    def connect(self) -> None:
        last_error: OSError | None = None
        for family, socktype, proto, _canon, addr in socket.getaddrinfo(
            self.host, self.port, socket.AF_INET, socket.SOCK_STREAM
        ):
            sock = socket.socket(family, socktype, proto)
            sock.settimeout(self.timeout)
            try:
                sock.connect(addr)
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                sock.close()
        raise last_error or OSError(f"{self.host} 没有可用的 IPv4 地址")


_SSL_CONTEXT = ssl.create_default_context()


class BiliClient:
    def __init__(self, sessdata: str = "", timeout: int = 15, interval: float = 0.5):
        self._sessdata = sessdata.strip()
        self.timeout = timeout
        self.interval = max(0.0, interval)
        self._last_request = 0.0

        self._headers = {
            "User-Agent": UA,
            "Referer": "https://www.bilibili.com/",
        }
        if self._sessdata:
            self._headers["Cookie"] = f"SESSDATA={self._sessdata}"

        self._wbi_keys: tuple[str, str] | None = None
        self._wbi_time = 0.0
        self._buvid_ready = False

    def _throttle(self):
        delta = time.monotonic() - self._last_request
        if delta < self.interval:
            time.sleep(self.interval - delta)
        self._last_request = time.monotonic()

    def request_raw(self, url: str, headers: dict[str, str] | None = None) -> bytes:
        self._throttle()
        merged = dict(self._headers)
        if headers:
            merged.update(headers)

        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https":
            path = parsed.path or "/"
            if parsed.query:
                path += "?" + parsed.query
            conn = _IPv4HTTPSConnection(
                parsed.hostname, parsed.port or 443, timeout=self.timeout, context=_SSL_CONTEXT
            )
            try:
                conn.request("GET", path, headers=merged)
                resp = conn.getresponse()
                status = resp.status
                resp_headers = resp.headers
                raw = resp.read()
            finally:
                conn.close()

            if status >= 400:
                raise urllib.error.HTTPError(url, status, resp.reason or "HTTP error", resp_headers, None)
            encoding = resp_headers.get("Content-Encoding", "")
        else:
            req = urllib.request.Request(url, headers=merged)
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                encoding = resp.headers.get("Content-Encoding", "")

        # 智能解压
        if raw[:2] == b"\x1f\x8b":
            return gzip.decompress(raw)
        if encoding == "deflate" or (raw[:1] not in (b"{", b"[", b"<")):
            try:
                return zlib.decompress(raw)
            except Exception:
                try:
                    return zlib.decompress(raw, -zlib.MAX_WBITS)
                except Exception:
                    return raw
        return raw

    def get_json(self, url: str, headers: dict[str, str] | None = None, retries: int = 2) -> dict:
        attempts = max(1, retries + 1)
        for attempt in range(attempts):
            try:
                raw = self.request_raw(url, headers=headers)
                data = json.loads(raw.decode("utf-8"))
                # B 站风控 -352 退避
                if data.get("code") == -352 and attempt < attempts - 1:
                    time.sleep(1.0 * (2**attempt))
                    continue
                return data
            except urllib.error.HTTPError as exc:
                if exc.code in (412, 429) and attempt < attempts - 1:
                    time.sleep(1.2 * (2**attempt))
                    continue
                raise
        return {}

    def ensure_buvid(self):
        if self._buvid_ready:
            return
        try:
            data = self.get_json("https://api.bilibili.com/x/frontend/finger/spi")
            b3 = data["data"]["b_3"]
            b4 = data["data"]["b_4"]
            import uuid

            cookie = f"buvid3={b3}; buvid4={b4}; _uuid={uuid.uuid4()}; b_nut={int(time.time())}"
            curr = self._headers.get("Cookie", "")
            self._headers["Cookie"] = f"{curr}; {cookie}" if curr else cookie
            self._buvid_ready = True
        except Exception:
            pass

    def wbi_keys(self) -> tuple[str, str]:
        if self._wbi_keys and time.monotonic() - self._wbi_time < 30:
            return self._wbi_keys
        data = self.get_json("https://api.bilibili.com/x/web-interface/nav")
        img_url = data["data"]["wbi_img"]["img_url"]
        sub_url = data["data"]["wbi_img"]["sub_url"]
        img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
        sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
        self._wbi_keys = (img_key, sub_key)
        self._wbi_time = time.monotonic()
        return self._wbi_keys

    def wbi_sign(self, params: dict) -> dict:
        img_key, sub_key = self.wbi_keys()
        signed = dict(params)
        signed["wts"] = int(time.time())
        signed = {k: "".join(ch for ch in str(v) if ch not in "!'()*") for k, v in signed.items()}
        query = urllib.parse.urlencode(sorted(signed.items()))
        orig = img_key + sub_key
        mixin_key = "".join(orig[i] for i in MIXIN_KEY_ENC_TAB)[:32]
        signed["w_rid"] = hashlib.md5((query + mixin_key).encode("utf-8")).hexdigest()
        return signed

    def resolve_ref(self, ref_or_url: str) -> str:
        """从任意文本/短链中提取 BV 号。"""
        text = ref_or_url.strip()
        if "b23.tv" in text:
            try:
                raw = self.request_raw(text)
                match = BV_RE.search(raw.decode("utf-8", "replace"))
                if match:
                    return match.group(0)
            except Exception:
                pass
        match = BV_RE.search(text)
        if match:
            return match.group(0)
        match = AV_RE.search(text)
        if match:
            return f"av{match.group(1)}"
        return text

    def get_video_meta(self, ref: str, page: int = 1) -> RawVideoMeta:
        bvid = self.resolve_ref(ref)
        if bvid.startswith("BV"):
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
        elif bvid.lower().startswith("av"):
            url = f"https://api.bilibili.com/x/web-interface/view?aid={bvid[2:]}"
        else:
            raise ValueError(f"无法识别的 B 站视频标识: {ref}")

        payload = self.get_json(url)
        data = payload.get("data") or {}
        if not data:
            raise RuntimeError(f"获取视频信息失败: {payload.get('message')}")

        pages = data.get("pages") or []
        page_idx = min(max(1, page), len(pages) or 1)
        p_data = pages[page_idx - 1] if pages else data
        stat = data.get("stat") or {}

        return RawVideoMeta(
            bvid=str(data.get("bvid") or bvid),
            aid=int(data.get("aid") or 0),
            cid=int(p_data.get("cid") or data.get("cid") or 0),
            page=page_idx,
            page_count=len(pages) or 1,
            title=str(data.get("title") or ""),
            desc=str(data.get("desc") or ""),
            up_name=str((data.get("owner") or {}).get("name") or ""),
            up_uid=int((data.get("owner") or {}).get("mid") or 0),
            duration=int(p_data.get("duration") or data.get("duration") or 0),
            pubdate=int(data.get("pubdate") or 0),
            views=int(stat.get("view") or 0),
            danmaku=int(stat.get("danmaku") or 0),
            replies=int(stat.get("reply") or 0),
            likes=int(stat.get("like") or 0),
            coins=int(stat.get("coin") or 0),
            favorites=int(stat.get("favorite") or 0),
            shares=int(stat.get("share") or 0),
        )

    def get_subtitles(self, bvid: str, cid: int) -> list[RawSubtitleItem]:
        """获取官方 CC 字幕或 AI 字幕"""
        params = self.wbi_sign({"bvid": bvid, "cid": cid})
        url = f"https://api.bilibili.com/x/player/wbi/v2?{urllib.parse.urlencode(params)}"
        payload = self.get_json(url)
        data = payload.get("data") or {}
        sub_list = (data.get("subtitle") or {}).get("subtitles") or []
        if not sub_list:
            return []

        # 优先选 zh-Hans 或 ai-zh
        chosen = None
        for lang_code in ("zh-Hans", "zh", "ai-zh", "en", "ja"):
            for item in sub_list:
                if str(item.get("lan") or "") == lang_code:
                    chosen = item
                    break
            if chosen:
                break
        if not chosen:
            chosen = sub_list[0]

        sub_url = str(chosen.get("subtitle_url") or "")
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        if not sub_url:
            return []

        raw_sub = self.get_json(sub_url)
        items: list[RawSubtitleItem] = []
        for body in raw_sub.get("body") or []:
            start = float(body.get("from") or 0.0)
            end = float(body.get("to") or (start + 2.0))
            text = str(body.get("content") or "").strip()
            if text:
                items.append(RawSubtitleItem(start=start, end=end, content=text))
        return items

    def get_danmaku(self, cid: int) -> list[RawDanmakuItem]:
        """获取视频全部弹幕"""
        url = f"https://api.bilibili.com/x/v1/dm/list.so?oid={cid}"
        try:
            raw = self.request_raw(url)
            text = raw.decode("utf-8", "replace")
        except Exception:
            return []

        items: list[RawDanmakuItem] = []
        # <d p="45.123,1,25,...">内容</d>
        for match in re.finditer(r'<d p="([^",]+)[^>]*>([^<]+)</d>', text):
            try:
                t = float(match.group(1))
                content = match.group(2).strip()
                if content:
                    items.append(RawDanmakuItem(time_point=t, text=content))
            except Exception:
                continue
        return items

    def get_comments(self, aid: int, limit: int = 20) -> list[RawComment]:
        """获取置顶与高赞评论"""
        url = f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&pn=1&ps={limit}&sort=2"
        payload = self.get_json(url)
        data = payload.get("data") or {}
        comments: list[RawComment] = []

        def parse_item(item: dict, pinned: bool = False):
            member = item.get("member") or {}
            content = (item.get("content") or {}).get("message") or ""
            like = int(item.get("like") or 0)
            if content:
                comments.append(
                    RawComment(
                        uname=str(member.get("uname") or "观众"),
                        content=str(content).strip()[:500],
                        like=like,
                        pinned=pinned,
                    )
                )

        # 优先读取 top 置顶评论
        for top in data.get("top_replies") or []:
            parse_item(top, pinned=True)
        # 读取常规热门评论
        for rep in data.get("replies") or []:
            parse_item(rep, pinned=False)

        return comments

    def get_stream_urls(self, bvid: str, cid: int) -> tuple[str | None, str | None]:
        """获取视频流（Dash Video 480P）与纯音频流（Dash Audio）。

        :return: (video_stream_url, audio_stream_url)
        """
        # 有登录态时请求 1080P (80) 或 720P (64)，游客态保底 360P (32)
        target_qn = 80 if self._sessdata else 32
        params = self.wbi_sign({"bvid": bvid, "cid": cid, "qn": target_qn, "fnval": 16, "fourk": 0})
        url = f"https://api.bilibili.com/x/player/wbi/playurl?{urllib.parse.urlencode(params)}"
        payload = self.get_json(url)
        data = payload.get("data") or {}
        dash = data.get("dash") or {}

        # 1. 提取视频流
        video_url: str | None = None
        videos = [v for v in dash.get("video") or [] if v.get("baseUrl") or v.get("base_url")]
        if videos:
            videos.sort(key=lambda x: int(x.get("height") or 0))
            # 选 480P 或 360P
            chosen = next((v for v in videos if int(v.get("height") or 0) >= 360), videos[0])
            video_url = str(chosen.get("baseUrl") or chosen.get("base_url"))

        # 2. 提取音频流
        audio_url: str | None = None
        audios = [a for a in dash.get("audio") or [] if a.get("baseUrl") or a.get("base_url")]
        if audios:
            # 取第一条可用音频流
            audio_url = str(audios[0].get("baseUrl") or audios[0].get("base_url"))

        # 如果没有 Dash，回退到 durl
        if not video_url:
            durls = [d for d in data.get("durl") or [] if d.get("url")]
            if durls:
                video_url = str(durls[0]["url"])

        return video_url, audio_url
