"""B 站二维码扫码登录与凭据提取引擎（QR Login Client）。

纯标准库 urllib 实现，无需 curl_cffi 或庞大依赖：
1. 申请登录二维码 (url + qrcode_key)；
2. 兼容 2024~2026 年 B 站改版后的 crossDomain 跨域票据链接跟随，从 Set-Cookie 中提取完整凭据；
3. 提取 SESSDATA, bili_jct, DedeUserID 与 refresh_token (ac_time_value)。
"""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger

QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class QrCodeSession:
    url: str
    qrcode_key: str
    qr_image_url: str  # 可直接访问的图片地址 (如 api.qrserver.com 生成)


@dataclass
class PollResult:
    code: int  # 0: 成功, 86101: 未扫码, 86090: 已扫码未确认, 86038: 二维码失效
    message: str
    credentials: dict[str, str] | None = None


class BiliQrLoginService:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def request_qrcode(self) -> QrCodeSession:
        """申请登录二维码"""
        req = urllib.request.Request(QR_GENERATE_URL, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("code") != 0:
            raise RuntimeError(f"申请登录二维码失败: {data.get('message')}")

        d = data.get("data") or {}
        url = str(d.get("url") or "")
        qrcode_key = str(d.get("qrcode_key") or "")

        if not url or not qrcode_key:
            raise RuntimeError("二维码生成返回数据异常")

        # 生成可直接发送/渲染的在线二维码图 URL (避免本地缺少 PIL/segno 时报错)
        encoded_url = urllib.parse.quote(url)
        qr_img_url = f"https://api.qrserver.com/v1/create-qr-code/?data={encoded_url}&size=280x280"

        return QrCodeSession(url=url, qrcode_key=qrcode_key, qr_image_url=qr_img_url)

    def poll_status(self, qrcode_key: str) -> PollResult:
        """轮询二维码扫码状态"""
        url = f"{QR_POLL_URL}?qrcode_key={qrcode_key}"
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        if data.get("code") != 0:
            return PollResult(code=-1, message=data.get("message") or "轮询请求失败")

        d = data.get("data") or {}
        poll_code = int(d.get("code", -1))
        msg = str(d.get("message") or "")

        if poll_code != 0:
            return PollResult(code=poll_code, message=msg)

        # 扫码确认成功！提取凭证 (兼容 crossDomain 票据与常规 URL)
        target_url = str(d.get("url") or "")
        refresh_token = str(d.get("refresh_token") or "")

        credentials = self._extract_credentials(target_url, refresh_token)
        return PollResult(code=0, message="登录成功", credentials=credentials)

    def _extract_credentials(self, target_url: str, refresh_token: str) -> dict[str, str]:
        """从成功响应中解析 Set-Cookie 与票据"""
        cookies: dict[str, str] = {}

        # 方式 1: 如果是 crossDomain 链接，跟随请求获取响应头里的 Set-Cookie
        if "crossDomain" in target_url:
            cj = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
            req = urllib.request.Request(target_url, headers={"User-Agent": UA, "Referer": "https://passport.bilibili.com/"})
            try:
                opener.open(req, timeout=self.timeout)
                for c in cj:
                    cookies[c.name] = c.value
            except Exception as exc:
                logger.warning(f"跟随 crossDomain 链接失败: {exc}")

        # 方式 2: 从 target_url 的 query 参数中回退解析
        if not cookies.get("SESSDATA"):
            parsed = urllib.parse.urlparse(target_url)
            qs = urllib.parse.parse_qs(parsed.query)
            for k in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"):
                val = qs.get(k)
                if val:
                    cookies[k] = val[0]

        return {
            "sessdata": cookies.get("SESSDATA", ""),
            "bili_jct": cookies.get("bili_jct", ""),
            "dedeuserid": cookies.get("DedeUserID", ""),
            "refresh_token": refresh_token,
        }
