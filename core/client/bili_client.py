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

from core.models import RawComment, RawDanmakuItem, RawSubtitleItem, RawVideoMeta, SubtitleTrack

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
PAGE_RE = re.compile(r"(?:[?&]|&amp;)(?:p|page)=(\d+)", re.IGNORECASE)


def extract_page_number(text: str) -> int:
    """从链接或重定向 HTML 中提取 1-based 分 P 序号。"""
    match = PAGE_RE.search(text or "")
    if not match:
        return 0
    try:
        return max(1, int(match.group(1)))
    except ValueError:
        return 0


def with_page(video_id: str, page: int) -> str:
    return f"{video_id}?p={page}" if page and page > 1 else video_id


@dataclass
class StreamInfo:
    """一次 playurl 解析结果。"""

    video_url: str | None = None
    audio_url: str | None = None
    width: int = 0
    height: int = 0
    quality: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.video_url)


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

    def media_headers(self) -> dict[str, str]:
        """给 ffmpeg 用的请求头（含登录 Cookie，1080P 流需要）。"""
        headers = {"Referer": "https://www.bilibili.com/", "User-Agent": UA}
        if self._sessdata:
            headers["Cookie"] = f"SESSDATA={self._sessdata}"
        return headers

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
        """从任意文本/短链中提取 BV/av 号；分 P 链接保留 ?p=N。"""
        text = ref_or_url.strip()
        page = extract_page_number(text)
        if "b23.tv" in text:
            try:
                raw_text = self.request_raw(text).decode("utf-8", "replace")
                page = page or extract_page_number(raw_text)
                match = BV_RE.search(raw_text)
                if match:
                    return with_page(match.group(0), page)
            except Exception:
                pass
        match = BV_RE.search(text)
        if match:
            return with_page(match.group(0), page)
        match = AV_RE.search(text)
        if match:
            return with_page(f"av{match.group(1)}", page)
        return text

    def get_video_meta(self, ref: str, page: int | None = None) -> RawVideoMeta:
        page = page or extract_page_number(ref) or 1
        raw_id = self.resolve_ref(ref)
        page = page or extract_page_number(raw_id) or 1
        bvid = raw_id.split("?", 1)[0]
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
        series_title = str(data.get("title") or "")
        part_title = str(p_data.get("part") or "").strip()
        if len(pages) > 1 and part_title:
            series_title = f"{series_title}｜P{page_idx} {part_title}"

        return RawVideoMeta(
            bvid=str(data.get("bvid") or bvid),
            aid=int(data.get("aid") or 0),
            cid=int(p_data.get("cid") or data.get("cid") or 0),
            page=page_idx,
            page_count=len(pages) or 1,
            title=series_title,
            desc=str(data.get("desc") or ""),
            up_name=str((data.get("owner") or {}).get("name") or ""),
            up_uid=int((data.get("owner") or {}).get("mid") or 0),
            duration=int(p_data.get("duration") or data.get("duration") or 0),
            pubdate=int(data.get("pubdate") or 0),
            tname=str(data.get("tname") or data.get("tname_v2") or ""),
            tags=self.get_tags(str(data.get("bvid") or bvid)),
            views=int(stat.get("view") or 0),
            danmaku=int(stat.get("danmaku") or 0),
            replies=int(stat.get("reply") or 0),
            likes=int(stat.get("like") or 0),
            coins=int(stat.get("coin") or 0),
            favorites=int(stat.get("favorite") or 0),
            shares=int(stat.get("share") or 0),
        )

    def get_tags(self, bvid: str) -> list[str]:
        """视频标签。失败不影响主流程。"""
        try:
            payload = self.get_json(
                f"https://api.bilibili.com/x/tag/archive/tags?bvid={urllib.parse.quote(bvid)}"
            )
            return [str(item.get("tag_name") or "") for item in payload.get("data") or [] if item.get("tag_name")]
        except Exception:
            return []

    def get_subtitle_track(self, bvid: str, cid: int) -> SubtitleTrack:
        """抓取字幕并保留来源信息。

        B 站的 `wbi/v2` 与 `player/v2` 两个接口在不同视频上返回并不一致：
        实测有视频只有 `player/v2` 能看到 AI 字幕，因此这里做双接口兜底。
        """
        sub_list: list[dict] = []
        try:
            params = self.wbi_sign({"bvid": bvid, "cid": cid})
            url = f"https://api.bilibili.com/x/player/wbi/v2?{urllib.parse.urlencode(params)}"
            payload = self.get_json(url)
            sub_list = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
        except Exception as exc:
            logger.warning(f"wbi 字幕接口失败，尝试 player/v2: {exc}")

        if not sub_list:
            try:
                url = f"https://api.bilibili.com/x/player/v2?bvid={urllib.parse.quote(bvid)}&cid={cid}"
                payload = self.get_json(url)
                sub_list = ((payload.get("data") or {}).get("subtitle") or {}).get("subtitles") or []
            except Exception as exc:
                return SubtitleTrack(error=f"字幕接口失败: {exc}")

        if not sub_list:
            return SubtitleTrack()

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
            return SubtitleTrack(lang=str(chosen.get("lan") or ""), error="字幕地址为空")

        try:
            raw_sub = self.get_json(sub_url)
        except Exception as exc:
            return SubtitleTrack(lang=str(chosen.get("lan") or ""), error=f"字幕内容下载失败: {exc}")

        items: list[RawSubtitleItem] = []
        for body in raw_sub.get("body") or []:
            start = float(body.get("from") or 0.0)
            end = float(body.get("to") or (start + 2.0))
            text = str(body.get("content") or "").strip()
            if text:
                items.append(RawSubtitleItem(start=start, end=end, content=text))

        lang = str(chosen.get("lan") or "")
        is_ai = lang.startswith("ai-") or int(chosen.get("type") or 0) == 1
        return SubtitleTrack(
            items=items,
            source="ai" if is_ai else "official",
            lang=lang,
            lang_doc=str(chosen.get("lan_doc") or ""),
        )

    def get_subtitles(self, bvid: str, cid: int) -> list[RawSubtitleItem]:
        """兼容旧调用：只返回字幕条目。"""
        return self.get_subtitle_track(bvid, cid).items

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

    def get_comments(self, aid: int, limit: int = 20, sort: int = 2, max_pages: int = 3) -> list[RawComment]:
        """获取置顶与评论。

        单页 ps 上限为 20；需要更多时按页拉取，并把楼中楼里同样有信息量的回复带上。
        """
        comments: list[RawComment] = []
        seen: set[str] = set()

        def parse_item(item: dict, pinned: bool = False):
            member = item.get("member") or {}
            content = (item.get("content") or {}).get("message") or ""
            like = int(item.get("like") or 0)
            if not content:
                return
            text = str(content).strip()
            if text in seen:
                return
            seen.add(text)
            replies = []
            for reply in item.get("replies") or []:
                msg = ((reply.get("content") or {}).get("message") or "").strip()
                if msg:
                    replies.append(msg[:300])
            comments.append(
                RawComment(
                    uname=str(member.get("uname") or "观众"),
                    content=text[:1200],
                    like=like,
                    pinned=pinned,
                    replies=replies[:3],
                )
            )

        page = 1
        while len(comments) < limit and page <= max(1, max_pages):
            url = (
                f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}"
                f"&pn={page}&ps=20&sort={sort}&nohot=1"
            )
            try:
                payload = self.get_json(url)
            except Exception:
                break
            data = payload.get("data") or {}
            if page == 1:
                for top in data.get("top_replies") or []:
                    parse_item(top, pinned=True)
            replies = data.get("replies") or []
            if not replies:
                break
            for rep in replies:
                parse_item(rep, pinned=False)
                if len(comments) >= limit:
                    break
            page += 1

        # 热门排序会把只有几个赞的「文字轴」压到后面；补一页按时间排序的最新评论
        if len(comments) < limit and limit >= 30:
            try:
                payload = self.get_json(
                    f"https://api.bilibili.com/x/v2/reply?type=1&oid={aid}&pn=1&ps=20&sort=0&nohot=1"
                )
                for rep in (payload.get("data") or {}).get("replies") or []:
                    parse_item(rep, pinned=False)
                    if len(comments) >= limit:
                        break
            except Exception:
                pass

        return comments[:limit]

    def get_stream_info(self, bvid: str, cid: int) -> "StreamInfo":
        """获取可用的最高画质视频流与音频流。

        旧实现按 height 升序取第一个 >=360 的流，登录后反而恒选 360P；
        这里改为取最高画质（最高 1080P，登录态不足时自动降级）。
        """
        target_qn = 120 if self._sessdata else 32
        params = self.wbi_sign(
            {"bvid": bvid, "cid": cid, "qn": target_qn, "fnval": 4048, "fourk": 1}
        )
        url = f"https://api.bilibili.com/x/player/wbi/playurl?{urllib.parse.urlencode(params)}"
        payload = self.get_json(url)
        data = payload.get("data") or {}
        dash = data.get("dash") or {}

        video_url: str | None = None
        width = height = 0
        videos = [v for v in dash.get("video") or [] if v.get("baseUrl") or v.get("base_url")]
        if videos:
            # 优先 AVC（兼容性最好），再按分辨率/码率取最高
            def rank(item: dict) -> tuple[int, int, int]:
                codec_ok = 1 if int(item.get("codecid") or 0) in (7, 0) else 0
                return (int(item.get("height") or 0), codec_ok, int(item.get("bandwidth") or 0))

            chosen = max(videos, key=rank)
            video_url = str(chosen.get("baseUrl") or chosen.get("base_url"))
            width = int(chosen.get("width") or 0)
            height = int(chosen.get("height") or 0)

        audio_url: str | None = None
        audios = [a for a in dash.get("audio") or [] if a.get("baseUrl") or a.get("base_url")]
        if audios:
            audios.sort(key=lambda x: int(x.get("bandwidth") or 0), reverse=True)
            audio_url = str(audios[0].get("baseUrl") or audios[0].get("base_url"))

        if not video_url:
            durls = [d for d in data.get("durl") or [] if d.get("url")]
            if durls:
                video_url = str(durls[0]["url"])

        return StreamInfo(
            video_url=video_url,
            audio_url=audio_url,
            width=width,
            height=height,
            quality=int(data.get("quality") or 0),
        )

    def get_stream_urls(self, bvid: str, cid: int) -> tuple[str | None, str | None]:
        """兼容旧调用：只要 (video_url, audio_url)。"""
        info = self.get_stream_info(bvid, cid)
        return info.video_url, info.audio_url
