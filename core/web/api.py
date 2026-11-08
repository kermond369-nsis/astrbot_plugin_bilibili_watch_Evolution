"""可视化设置页的后端 Web API。

路由统一以插件目录名开头（Dashboard 要求），页面通过 bridge 调用时不带前缀。
所有 handler 都从 `core.web.http` 读取请求，兼容 AstrBot 新 Web API 与旧 Quart 运行时。
"""

from __future__ import annotations

import base64
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

try:  # 允许在无 AstrBot 运行时的离线环境里单测
    from astrbot.api import logger
except Exception:  # pragma: no cover
    import logging

    logger = logging.getLogger("bilibili_watch")

from core.client.media_extractor import MediaExtractor
from core.config import AppConfig
from core.web import cache as cache_service
from core.web.http import client_username, json_body, query_get
from core.web.spec import (
    ALL_FIELDS,
    apply_defaults,
    mask_tail,
    public_values,
    spec_payload,
    validate_values,
)

NAV_URL = "https://api.bilibili.com/x/web-interface/nav"


def _probe_ffmpeg(exe: str) -> dict:
    try:
        proc = subprocess.run(
            [exe, "-version"], capture_output=True, timeout=6, check=False
        )
        raw = (proc.stdout or b"") + (proc.stderr or b"")
        text = raw.decode("utf-8", "replace").strip().splitlines()
        return {
            "path": exe,
            "available": proc.returncode == 0,
            "version": text[0] if text else "",
        }
    except Exception as exc:
        return {"path": exe, "available": False, "version": str(exc)}


class PluginWebAPI:
    def __init__(
        self,
        plugin: Any,
        plugin_root: Path,
        data_dir: Path,
        plugin_name: str,
        version: str,
    ):
        self.plugin = plugin
        self.plugin_root = Path(plugin_root)
        self.data_dir = Path(data_dir)
        self.plugin_name = plugin_name
        self.version = version
        self._ffmpeg_cache: tuple[float, dict] | None = None
        self._account_cache: tuple[float, dict] | None = None
        self._cache_cache: tuple[float, dict] | None = None
        self._registered: list[str] = []

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------
    def register(self, context) -> None:
        register = getattr(context, "register_web_api", None)
        if register is None:
            logger.warning("[预知眼] 当前 AstrBot 不支持 register_web_api，可视化设置页不可用")
            return
        routes = [
            ("/status", self.get_status, ["GET"], "插件状态"),
            ("/settings", self.get_settings, ["GET"], "读取设置"),
            ("/settings/save", self.save_settings, ["POST"], "保存设置"),
            ("/settings/reset", self.reset_settings, ["POST"], "恢复默认设置"),
            ("/login/qr", self.login_qr, ["POST"], "申请扫码登录二维码"),
            ("/login/status", self.login_status, ["GET"], "轮询扫码状态"),
            ("/login/account", self.login_account, ["GET"], "读取登录账号"),
            ("/login/logout", self.login_logout, ["POST"], "清除扫码凭据"),
            ("/cache", self.get_cache, ["GET"], "缓存占用"),
            ("/cache/cleanup", self.cleanup_cache, ["POST"], "清理缓存"),
            ("/videos", self.get_videos, ["GET"], "最近观看"),
            ("/diagnostics", self.get_diagnostics, ["GET"], "运行诊断"),
        ]
        for suffix, handler, methods, desc in routes:
            route = f"/{self.plugin_name}{suffix}"
            try:
                register(route, handler, methods, desc)
                self._registered.append(route)
            except Exception as exc:
                logger.warning(f"[预知眼] 注册 Web API 失败 {route}: {exc}")
        logger.info(f"[预知眼] 可视化设置页已注册 {len(self._registered)} 个接口")

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------
    async def _run(self, func, *args):
        return await self.plugin._run_in_pool(func, *args)

    def _config_dict(self) -> dict:
        config_obj = getattr(self.plugin, "_plugin_config", None)
        if isinstance(config_obj, dict):
            base = dict(config_obj)
        else:
            base = asdict(self.plugin.cfg)
        for item in ALL_FIELDS:
            base.setdefault(item.key, getattr(self.plugin.cfg, item.key, item.default))
        return base

    def _config_path(self) -> str:
        config_obj = getattr(self.plugin, "_plugin_config", None)
        path = getattr(config_obj, "config_path", "") if config_obj is not None else ""
        return str(path or (self.plugin_root / "config.json"))

    def _login_summary(self) -> dict:
        qr_sess = self.plugin._cred_manager.get_sessdata()
        cfg_sess = str(getattr(self.plugin.cfg, "sessdata", "") or "").strip()
        if qr_sess:
            source = "qr"
        elif cfg_sess:
            source = "config"
        else:
            source = "guest"
        account = None
        if self._account_cache and time.time() - self._account_cache[0] < 600:
            account = self._account_cache[1]
        return {
            "logged_in": bool(qr_sess or cfg_sess),
            "source": source,
            "sessdata_tail": mask_tail(qr_sess or cfg_sess),
            "account": account,
        }

    def _asr_configured(self) -> bool:
        provider = str(getattr(self.plugin.cfg, "asr_provider", "none") or "none").lower()
        if provider == "mimo":
            return bool(str(getattr(self.plugin.cfg, "mimo_api_key", "") or "").strip())
        if provider == "siliconflow":
            return bool(str(getattr(self.plugin.cfg, "siliconflow_api_key", "") or "").strip())
        return False

    async def _cache_summary(self, force: bool = False) -> dict:
        now = time.time()
        if not force and self._cache_cache and now - self._cache_cache[0] < 10:
            return self._cache_cache[1]
        summary = await self._run(cache_service.summarize_cache, self.data_dir)
        self._cache_cache = (now, summary)
        return summary

    async def _ffmpeg_info(self) -> dict:
        now = time.time()
        if self._ffmpeg_cache and now - self._ffmpeg_cache[0] < 300:
            return self._ffmpeg_cache[1]
        try:
            exe = self.plugin._media_extractor.get_ffmpeg_exe()
        except Exception:
            exe = ""
        info = await self._run(_probe_ffmpeg, str(exe or "ffmpeg"))
        self._ffmpeg_cache = (now, info)
        return info

    async def _fetch_avatar(self, url: str) -> str:
        """把 B 站头像下载为 data URL，避免前端受防盗链/CSP 影响。"""
        if not url.startswith(("http://", "https://")):
            return ""
        try:
            client = self.plugin._get_bili_client()
            raw = await self._run(client.request_raw, url)
        except Exception as exc:
            logger.debug(f"[预知眼] 头像下载失败: {exc}")
            return ""
        if not raw or len(raw) > 512 * 1024:
            return ""
        if raw.startswith(b"\xff\xd8"):
            mime = "image/jpeg"
        elif raw.startswith(b"\x89PNG"):
            mime = "image/png"
        elif raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            mime = "image/webp"
        else:
            return ""
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    async def _fetch_account(self, force: bool = False) -> dict | None:
        if not force and self._account_cache and time.time() - self._account_cache[0] < 600:
            return self._account_cache[1]
        if not self._login_summary()["logged_in"]:
            return None
        try:
            client = self.plugin._get_bili_client()
            payload = await self._run(client.get_json, NAV_URL)
        except Exception as exc:
            logger.warning(f"[预知眼] 读取登录账号失败: {exc}")
            return None
        data = payload.get("data") or {}
        if not data.get("isLogin"):
            return None
        face = str(data.get("face") or "")
        account = {
            "uname": str(data.get("uname") or ""),
            "mid": str(data.get("mid") or ""),
            "level": int((data.get("level_info") or {}).get("current_level") or 0),
            "vip": int(data.get("vipStatus") or 0) == 1,
            "coins": int(data.get("money") or 0),
            "avatar": await self._fetch_avatar(face) if face else "",
        }
        self._account_cache = (time.time(), account)
        return account

    # ------------------------------------------------------------------
    # 状态 / 诊断
    # ------------------------------------------------------------------
    async def get_status(self) -> dict:
        cache = await self._cache_summary()
        ffmpeg = await self._ffmpeg_info()
        cfg = self.plugin.cfg
        vision = {
            "mode": "override" if cfg.vision_provider_id else "global",
            "provider_id": cfg.vision_provider_id,
            "model": "",
        }
        if not cfg.vision_provider_id:
            getter = getattr(self.plugin, "_global_provider", None)
            provider = None
            if getter is not None:
                try:
                    provider = await getter()
                except Exception:
                    provider = None
            if provider is not None:
                vision["provider_id"] = str(
                    (getattr(provider, "provider_config", None) or {}).get("id") or ""
                )
                vision["model"] = str(getattr(provider, "model_name", "") or "")
            else:
                vision["mode"] = "unavailable"
        return {
            "version": self.version,
            "plugin_name": self.plugin_name,
            "data_dir": str(self.data_dir),
            "config_path": self._config_path(),
            "login": self._login_summary(),
            "asr": {
                "provider": cfg.asr_provider,
                "configured": self._asr_configured(),
                "max_seconds": cfg.asr_max_seconds,
                "chunk_seconds": cfg.asr_chunk_seconds,
            },
            "vision_provider_id": cfg.vision_provider_id,
            "vision": vision,
            "gameplay_mode": cfg.gameplay_mode,
            "ffmpeg": ffmpeg,
            "cache": cache,
            "quota": {
                "user_daily_quota": cfg.user_daily_quota,
                "quota_exempt_admins": cfg.quota_exempt_admins,
                "cooldown_seconds": cfg.cooldown_seconds,
            },
            "registered_apis": list(self._registered),
            "server_time": int(time.time()),
        }

    async def get_diagnostics(self) -> dict:
        status = await self.get_status()
        data = {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "cwd": str(Path.cwd()),
            "registered_apis": list(self._registered),
            "cache_root": str(self.data_dir),
            "config_path": status.get("config_path", ""),
            "ffmpeg": status.get("ffmpeg", {}),
            "client_user": client_username(),
        }
        return data

    # ------------------------------------------------------------------
    # 设置
    # ------------------------------------------------------------------
    async def get_settings(self) -> dict:
        current = self._config_dict()
        values, secrets_present = public_values(current)
        return {
            "groups": spec_payload(),
            "values": values,
            "secrets_present": secrets_present,
            "config_path": self._config_path(),
        }

    async def _persist(self, values: dict) -> dict:
        if not values:
            return self._config_dict()
        config_obj = getattr(self.plugin, "_plugin_config", None)
        saved: dict
        if config_obj is not None and hasattr(config_obj, "save_config_async"):
            await config_obj.save_config_async(values)
            saved = dict(config_obj)
        elif config_obj is not None and hasattr(config_obj, "save_config"):
            config_obj.save_config(values)
            saved = dict(config_obj)
        else:
            path = self.plugin_root / "config.json"
            data: dict = {}
            if path.exists():
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            data.update(values)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            saved = data

        # 热重载插件侧配置与依赖对象
        self.plugin.cfg = AppConfig.load(self.plugin_root, saved)
        self.plugin._media_extractor = MediaExtractor(self.plugin.cfg.ffmpeg_path)
        transcripts = getattr(self.plugin, "_transcripts", None)
        if transcripts is not None:
            transcripts.ttl_seconds = max(1, int(self.plugin.cfg.transcript_ttl_hours)) * 3600
        self._ffmpeg_cache = None
        return self._config_dict()

    async def save_settings(self) -> dict:
        try:
            payload = await json_body()
            values = payload.get("values") if isinstance(payload.get("values"), dict) else payload
            current = self._config_dict()
            clean, errors = validate_values(values, current)
            if errors:
                return {"status": "error", "message": errors[0], "errors": errors}
            if not clean:
                return {"saved": False, "message": "没有需要保存的更改", "values": public_values(current)[0]}
            await self._persist(clean)
            current = self._config_dict()
            values_out, secrets_present = public_values(current)
            logger.info(f"[预知眼] 设置页保存了 {len(clean)} 项配置")
            return {
                "saved": True,
                "message": f"已保存 {len(clean)} 项设置",
                "values": values_out,
                "secrets_present": secrets_present,
            }
        except Exception as exc:
            logger.error(f"[预知眼] 保存设置失败: {exc}", exc_info=True)
            return {"status": "error", "message": f"保存失败：{exc}"}

    async def reset_settings(self) -> dict:
        try:
            payload = await json_body()
            keys = payload.get("keys") if isinstance(payload.get("keys"), list) else None
            current = self._config_dict()
            values = apply_defaults(current, keys)
            await self._persist(values)
            current = self._config_dict()
            values_out, secrets_present = public_values(current)
            logger.info(f"[预知眼] 设置页恢复默认 {len(values)} 项")
            return {
                "saved": True,
                "message": f"已恢复 {len(values)} 项默认值",
                "values": values_out,
                "secrets_present": secrets_present,
            }
        except Exception as exc:
            logger.error(f"[预知眼] 恢复默认失败: {exc}", exc_info=True)
            return {"status": "error", "message": f"恢复失败：{exc}"}

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------
    async def login_qr(self) -> dict:
        try:
            session = await self._run(
                self.plugin._qr_service.request_qrcode, self.data_dir / "qrcode"
            )
        except Exception as exc:
            logger.warning(f"[预知眼] 申请登录二维码失败: {exc}")
            return {"status": "error", "message": f"申请二维码失败：{exc}"}

        image = None
        if session.qr_image_path:
            try:
                raw = Path(session.qr_image_path).read_bytes()
                image = "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
                Path(session.qr_image_path).unlink(missing_ok=True)
            except Exception:
                image = None
        return {
            "key": session.qrcode_key,
            "url": session.url,
            "image": image,
            "expires_in": 180,
        }

    async def login_status(self) -> dict:
        key = str(query_get("key", "") or "").strip()
        if not key:
            return {"status": "error", "message": "缺少 qrcode_key"}
        try:
            result = await self._run(self.plugin._qr_service.poll_status, key)
        except Exception as exc:
            return {"state": "error", "code": -1, "message": f"轮询失败：{exc}"}

        if result.code == 0 and result.credentials:
            if not str(result.credentials.get("sessdata") or "").strip():
                return {
                    "state": "error",
                    "code": 0,
                    "message": "登录已确认，但没有取到 SESSDATA；请刷新二维码重试。",
                }
            self.plugin._cred_manager.save(result.credentials)
            self._account_cache = None
            account = await self._fetch_account(force=True)
            return {
                "state": "confirmed",
                "code": 0,
                "message": "登录成功",
                "account": account,
                "login": self._login_summary(),
            }
        if result.code == 86038:
            return {"state": "expired", "code": result.code, "message": "二维码已过期"}
        if result.code == 86090:
            return {"state": "scanned", "code": result.code, "message": "已扫码，请在手机上确认"}
        if result.code == 86101:
            return {"state": "waiting", "code": result.code, "message": "等待扫码"}
        return {
            "state": "waiting" if result.code > 0 else "error",
            "code": result.code,
            "message": result.message or "未知状态",
        }

    async def login_account(self) -> dict:
        login = self._login_summary()
        if not login["logged_in"]:
            return {"logged_in": False, "account": None}
        force = str(query_get("refresh", "0")) in ("1", "true", "yes")
        account = await self._fetch_account(force=force)
        return {"logged_in": True, "account": account, "source": login["source"]}

    async def login_logout(self) -> dict:
        try:
            self.plugin._cred_manager.clear()
        except Exception as exc:
            return {"status": "error", "message": f"清除凭据失败：{exc}"}
        self._account_cache = None
        login = self._login_summary()
        return {"cleared": True, "message": "已清除扫码凭据", "login": login}

    # ------------------------------------------------------------------
    # 缓存 / 最近观看
    # ------------------------------------------------------------------
    async def get_cache(self) -> dict:
        return await self._cache_summary(force=True)

    async def cleanup_cache(self) -> dict:
        payload = await json_body()
        target = str(payload.get("target") or "all").strip().lower()
        active = getattr(self.plugin, "_active_jobs", {}) or {}
        if target in ("frames", "cache", "all") and active:
            return {
                "status": "error",
                "message": "有视频正在处理中，暂时不能清理截图或临时音频缓存；请等任务结束后再试。",
            }
        try:
            result = await self._run(cache_service.cleanup_cache, self.data_dir, target)
        except Exception as exc:
            return {"status": "error", "message": f"清理失败：{exc}"}
        summary = await self._cache_summary(force=True)
        logger.info(f"[预知眼] 缓存清理 {target}: 释放 {result['freed_bytes']} 字节")
        return {"ok": True, "freed_bytes": result["freed_bytes"], "removed_files": result["removed_files"], "cache": summary}

    async def get_videos(self) -> dict:
        store = getattr(self.plugin, "_session_store", None)
        if store is None or not hasattr(store, "list_recent"):
            return {"items": []}
        try:
            items = await self._run(store.list_recent, 12)
        except Exception:
            items = []
        return {"items": items}
