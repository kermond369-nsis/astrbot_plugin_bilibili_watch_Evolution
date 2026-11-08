"""设置页 Web API 的离线单测：保存配置、扫码登录、缓存清理、最近观看。

用假插件对象替代 AstrBot 运行时；不访问网络。
"""

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.client.media_extractor import MediaExtractor
from core.config import AppConfig
from core.models import CleanedVideoBundle, RawVideoMeta
from core.perception.session_store import SessionStore
from core.web import api as web_api
from core.web.api import PluginWebAPI


class FakeCredentialManager:
    def __init__(self):
        self.sessdata = ""
        self.cleared = False

    def get_sessdata(self):
        return self.sessdata

    def save(self, credentials):
        self.sessdata = str(credentials.get("sessdata") or "")

    def clear(self):
        self.sessdata = ""
        self.cleared = True


class FakeQrService:
    def __init__(self):
        self.qr_path = None

    def request_qrcode(self, qr_dir):
        from core.auth.qr_login import QrCodeSession

        path = Path(qr_dir) / "qr.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
        self.qr_path = path
        return QrCodeSession(url="https://passport.example/qr", qrcode_key="key-1", qr_image_path=path)

    def poll_status(self, key):
        from core.auth.qr_login import PollResult

        return PollResult(code=0, message="ok", credentials={"sessdata": "sess-1"})


class FakePlugin:
    def __init__(self, root: Path, data: Path):
        self.cfg = AppConfig()
        self._plugin_config = None
        self._media_extractor = MediaExtractor("ffmpeg")
        self._transcripts = SimpleNamespace(ttl_seconds=0)
        self._session_store = SessionStore()
        self._cred_manager = FakeCredentialManager()
        self._qr_service = FakeQrService()
        self._active_jobs = {}

    async def _run_in_pool(self, func, *args):
        return func(*args)

    def _get_bili_client(self):
        raise RuntimeError("offline test: no bili client")


def bundle() -> CleanedVideoBundle:
    meta = RawVideoMeta(
        bvid="BV1MwxY63Eu9",
        aid=1,
        cid=2,
        title="蓝甲赛特塔124层",
        desc="",
        up_name="up",
        up_uid=3,
        duration=374,
        pubdate=0,
    )
    return CleanedVideoBundle(
        meta=meta,
        clean_desc="",
        timestamps_outline=[],
        subtitles=[],
        pinned_comment=None,
        curated_comments=[],
        high_energy_points=[],
        gameplay=True,
    )


async def run_async() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        data = Path(tmp) / "data"
        root.mkdir()
        data.mkdir()
        (data / "frames" / "BV1").mkdir(parents=True)
        (data / "frames" / "BV1" / "a.jpg").write_bytes(b"x" * 128)
        (data / "transcripts").mkdir()
        (data / "transcripts" / "BV1.json").write_text("{}", encoding="utf-8")
        (data / "credentials.json").write_text("{}", encoding="utf-8")

        plugin = FakePlugin(root, data)
        plugin._session_store.put(
            "aiocqhttp:GroupMessage:1", bundle(), "digest", frame_paths=["/tmp/a.jpg"]
        )
        api = PluginWebAPI(plugin, root, data, "astrbot_plugin_bilibili_watch", "1.1.5")

        print(">>> [测试 1] 状态 / 设置 / 最近观看 / 诊断...")
        status = await api.get_status()
        assert status["version"] == "1.1.5"
        assert status["cache"]["total_bytes"] >= 130
        assert status["login"]["logged_in"] is False

        settings = await api.get_settings()
        assert len(settings["groups"]) == 6
        assert len(settings["values"]) >= 15
        assert "mimo_api_key" not in settings["values"]

        videos = await api.get_videos()
        assert videos["items"][0]["bvid"] == "BV1MwxY63Eu9"
        assert videos["items"][0]["gameplay"] is True

        diagnostics = await api.get_diagnostics()
        assert diagnostics["python"] and "registered_apis" in diagnostics
        print("  -> 状态/分组/最近观看/诊断正常")

        print(">>> [测试 2] 保存设置并热重载...")
        payload = {"values": {"user_daily_quota": 33, "mimo_api_key": "sk-test", "transcript_ttl_hours": 12}}
        original_json = web_api.json_body

        async def fake_json(default=None):
            return payload

        web_api.json_body = fake_json
        try:
            result = await api.save_settings()
            assert result.get("saved") is True, result
            assert plugin.cfg.user_daily_quota == 33
            assert plugin.cfg.mimo_api_key == "sk-test"
            assert plugin._transcripts.ttl_seconds == 12 * 3600
            saved = json.loads((root / "config.json").read_text(encoding="utf-8"))
            assert saved["user_daily_quota"] == 33

            settings = await api.get_settings()
            assert settings["values"]["user_daily_quota"] == 33
            assert settings["secrets_present"]["mimo_api_key"] is True

            payload = {"keys": ["user_daily_quota"]}
            reset = await api.reset_settings()
            assert reset.get("saved") is True
            assert plugin.cfg.user_daily_quota == 20
            assert plugin.cfg.mimo_api_key == "sk-test", "恢复默认不应清空密钥"
        finally:
            web_api.json_body = original_json
        print("  -> 保存/热重载/恢复默认均正确")

        print(">>> [测试 3] 扫码登录流程...")
        qr = await api.login_qr()
        assert qr["key"] == "key-1"
        assert qr["image"].startswith("data:image/png;base64,")
        assert not plugin._qr_service.qr_path.exists(), "二维码 PNG 读取后应删除"

        original_query = web_api.query_get
        web_api.query_get = lambda name, default=None: "key-1"
        try:
            poll = await api.login_status()
            assert poll["state"] == "confirmed"
            assert plugin._cred_manager.get_sessdata() == "sess-1"
            assert poll["login"]["logged_in"] is True

            account = await api.login_account()
            assert account["logged_in"] is True
        finally:
            web_api.query_get = original_query

        out = await api.login_logout()
        assert out["cleared"] is True
        assert plugin._cred_manager.cleared is True
        print("  -> 生成二维码/确认登录/账号/退出均正确")

        print(">>> [测试 4] 缓存清理与任务保护...")
        original_json = web_api.json_body

        async def cleanup_json(default=None):
            return {"target": "transcripts"}

        web_api.json_body = cleanup_json
        try:
            result = await api.cleanup_cache()
            assert result["ok"] is True
            assert not (data / "transcripts").exists()
            assert (data / "credentials.json").exists()

            async def frames_json(default=None):
                return {"target": "frames"}

            web_api.json_body = frames_json
            plugin._active_jobs["x"] = object()
            result = await api.cleanup_cache()
            assert result.get("status") == "error", "有任务时应拒绝清理截图"
        finally:
            web_api.json_body = original_json
        print("  -> 分类清理/凭据保护/任务保护均正确")


class FakeAstrBotConfig(dict):
    """模拟 AstrBot 的 AstrBotConfig：dict 子类 + save_config_async。"""

    def __init__(self, data: dict):
        super().__init__(data)
        self.config_path = "C:/AstrBot/data/config/fake_config.json"
        self.saved = []

    async def save_config_async(self, values, indent=2):
        self.update(values)
        self.saved.append(dict(values))
        return True


async def run_astrbot_config_case() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "plugin"
        data = Path(tmp) / "data"
        root.mkdir()
        data.mkdir()
        plugin = FakePlugin(root, data)
        plugin._plugin_config = FakeAstrBotConfig(
            {
                "user_daily_quota": 20,
                "cooldown_seconds": 15,
                "ack_template": "hi",
                "mimo_api_key": "sk-old",
            }
        )
        api = PluginWebAPI(plugin, root, data, "astrbot_plugin_bilibili_watch", "1.1.5")
        original_json = web_api.json_body

        async def fake_json(default=None):
            return {"values": {"cooldown_seconds": 7, "mimo_api_key": "sk-new"}}

        web_api.json_body = fake_json
        try:
            result = await api.save_settings()
            assert result.get("saved") is True, result
            assert plugin._plugin_config.saved, "未走 AstrBotConfig.save_config_async"
            assert plugin.cfg.cooldown_seconds == 7
            assert plugin.cfg.mimo_api_key == "sk-new"
            assert not (root / "config.json").exists(), "AstrBot 环境下不应回退写 config.json"
            settings = await api.get_settings()
            assert settings["config_path"].endswith("fake_config.json")
        finally:
            web_api.json_body = original_json
    print("  -> AstrBotConfig 保存分支正常")


def main() -> None:
    print("=" * 52)
    print("开始执行设置页 Web API 离线验证...")
    print("=" * 52)
    asyncio.run(run_async())
    asyncio.run(run_astrbot_config_case())
    print("=" * 52)
    print("[PASS] Web API tests passed successfully!")
    print("=" * 52)


if __name__ == "__main__":
    main()
