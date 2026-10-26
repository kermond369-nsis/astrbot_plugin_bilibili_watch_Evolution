"""astrbot_plugin_bilibili_watch

智能清洗与双模门控的 B 站多模态感知插件。
子代理（眼睛）在隔离上下文提炼客观事实，主模型（脑子）以自身人设与语气自然发声。
"""

from __future__ import annotations

import asyncio
import functools
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parent
if str(PLUGIN_DIR) not in sys.path:
    sys.path.insert(0, str(PLUGIN_DIR))

# 热重载模块刷新，防止旧字节码残留
for mod_name in list(sys.modules.keys()):
    if mod_name.startswith("core.") or mod_name == "core":
        sys.modules.pop(mod_name, None)

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path

from core.auth.credential_manager import CredentialManager
from core.auth.qr_login import BiliQrLoginService

from core.asr.engine_mimo import MimoASREngine
from core.asr.engine_openai_compatible import OpenAICompatibleASREngine
from core.cleaner.asr_cleaner import convert_asr_text_to_subtitles
from core.cleaner.frame_filter import filter_and_deduplicate_frames
from core.cleaner.pipeline import run_clean_pipeline
from core.client.bili_client import BiliClient
from core.client.card_extractor import extract_bilibili_card_url
from core.client.media_extractor import MediaExtractor
from core.client.up_extractor import fetch_up_profile, normalize_up_query
from core.config import AppConfig
from core.gating.router import GatingRouter
from core.models import CleanedVideoBundle, GatingPlan, ProcessingMode
from core.perception.prompt_builder import (
    MEME_EXTRACTOR_SYSTEM_PROMPT,
    TUTORIAL_EXTRACTOR_SYSTEM_PROMPT,
    build_analysis_input,
)
from core.perception.session_store import SessionStore

PLUGIN_NAME = "astrbot_plugin_bilibili_watch"
PLUGIN_VERSION = "1.1.3"  # 与 metadata.yaml 保持一致
LOG = "[B站之眼evolution]"

# 运行时数据统一放在 AstrBot 标准插件数据目录：data/plugin_data/<插件名>
# （凭据、ASR 缓存音频、关键帧截图都写到这里，便于用户备份/迁移/审计）
PLUGIN_DATA_DIR = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME


@dataclass
class ActiveJob:
    started: float
    stage: str
    bvid: str = ""
    title: str = ""


class BilibiliWatchPlugin(Star):
    def __init__(self, context: Context, config=None):
        super().__init__(context)
        # 统一分层配置读取：WebUI 传入 > 本地 config.json > 环境变量
        self.cfg = AppConfig.load(PLUGIN_DIR, config)
        PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._cred_manager = CredentialManager(PLUGIN_DATA_DIR)
        self._qr_service = BiliQrLoginService()
        self._session_store = SessionStore(ttl_seconds=1800)
        self._active_jobs: dict[str, ActiveJob] = {}
        self._pending_triggers: dict[str, dict] = {}
        self._cooldown: dict[str, float] = {}
        self._user_quota: dict[str, tuple[str, int]] = {}

        # 独立线程池，完全不阻塞 AstrBot 事件循环
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="bili-watch")
        self._semaphore = asyncio.Semaphore(2)

        # 核心服务组件
        self._media_extractor = MediaExtractor(self.cfg.ffmpeg_path)
        self._router = GatingRouter()

        logger.info(f"{LOG} 插件已加载 (版本 {PLUGIN_VERSION}，智能清洗与双模门控模式)")

    async def terminate(self) -> None:
        try:
            self._executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            pass
        self._active_jobs.clear()
        self._pending_triggers.clear()
        logger.info(f"{LOG} 插件已卸载")

    # -- 辅助工具 -----------------------------------------------------

    async def _run_in_pool(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, functools.partial(func, *args))

    def _get_bili_client(self) -> BiliClient:
        # 优先级：已扫码的本地持久化凭据 > WebUI/config.json/环境变量传入的凭据
        sessdata = self._cred_manager.get_sessdata() or self.cfg.sessdata
        return BiliClient(sessdata=sessdata)

    def _get_asr_engine(self):
        provider = self.cfg.asr_provider.lower()
        if provider == "mimo":
            key = self.cfg.mimo_api_key
            return MimoASREngine(api_key=key, base_url=self.cfg.mimo_base_url) if key else None
        elif provider == "siliconflow":
            key = self.cfg.siliconflow_api_key
            return (
                OpenAICompatibleASREngine(
                    api_key=key,
                    base_url=self.cfg.siliconflow_base_url,
                    model=self.cfg.siliconflow_model,
                )
                if key
                else None
            )
        return None

    # -- 权限与流控 ---------------------------------------------------

    def _quota_ok(self, event: AstrMessageEvent) -> bool:
        quota = self.cfg.user_daily_quota
        if quota <= 0:
            return True
        sender = str(event.get_sender_id() or "")
        if not sender:
            return True
        if self.cfg.quota_exempt_admins:
            is_admin = getattr(event, "is_admin", False)
            if is_admin() if callable(is_admin) else is_admin:
                return True
        today = time.strftime("%Y-%m-%d")
        key = f"{today}|{sender}"
        used = self._user_quota.get(key, (today, 0))[1]
        if used >= quota:
            return False
        self._user_quota[key] = (today, used + 1)
        return True

    def _cooldown_ok(self, umo: str) -> bool:
        cd = self.cfg.cooldown_seconds
        now = time.monotonic()
        if now - self._cooldown.get(umo, 0) < cd:
            return False
        self._cooldown[umo] = now
        return True

    # -- 触发与等待安抚 -----------------------------------------------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1001)
    async def on_waiting_notice(self, event: AstrMessageEvent):
        """安抚状态拦截：看片过程中群友若催促，发送耗时安抚，同时放行核心指令。"""
        if not self.cfg.wait_notice_enabled:
            return
        if not getattr(event, "is_at_or_wake_command", False):
            return
        umo = event.unified_msg_origin
        job = self._active_jobs.get(umo)
        if not job:
            return

        text = getattr(event, "message_str", "") or ""
        # 白名单核心系统指令放行
        if any(text.strip().lstrip("/").lower().startswith(c) for c in ("stop", "reset", "new", "help")):
            return

        elapsed = int(time.monotonic() - job.started)
        await event.send(
            MessageChain([Plain(f"还在看呢…已经看了 {elapsed} 秒（当前阶段：{job.stage}），别催啦～")])
        )
        event.stop_event()

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1000)
    async def on_message_detect(self, event: AstrMessageEvent):
        """自然语言触发：@bot 带有 B 站链接时登记任务。"""
        if not getattr(event, "is_at_or_wake_command", False):
            return
        umo = event.unified_msg_origin
        if umo in self._active_jobs or umo in self._pending_triggers:
            return
        if not self._cooldown_ok(umo):
            return

        text = getattr(event, "message_str", "") or ""
        client = self._get_bili_client()
        # b23.tv 短链需要真实网络跳转，放线程池执行，避免阻塞事件循环
        ref = await self._run_in_pool(client.resolve_ref, text)
        if not ref.startswith("BV") and not ref.lower().startswith("av"):
            # 尝试从小程序卡片或引用回复链中解析
            card_url = extract_bilibili_card_url(event)
            if card_url:
                ref = await self._run_in_pool(client.resolve_ref, card_url)
                logger.info(f"{LOG} 成功从小程序卡片/引用链提取到 B 站链接: {card_url}")

        if not ref.startswith("BV") and not ref.lower().startswith("av"):
            return

        if not self._quota_ok(event):
            await event.send(MessageChain([Plain("今天看视频的次数用完啦，明天再来找我吧！")]))
            event.stop_event()
            return

        self._pending_triggers[umo] = {"ref": ref}

    # -- 核心处理与主模型注入 -----------------------------------------

    @filter.on_llm_request(priority=900)
    async def on_llm_request_hook(self, event: AstrMessageEvent, req):
        """在主模型发起回复前，处理视频并将提炼事实注入 req.prompt。"""
        umo = event.unified_msg_origin
        pending = self._pending_triggers.pop(umo, None)

        # 检查是否为对上一视频的追问
        if not pending:
            user_msg = getattr(event, "message_str", "") or ""
            if self._session_store.is_followup_message(umo, user_msg):
                followup_prompt = self._session_store.build_followup_prompt(umo, user_msg)
                if followup_prompt:
                    if not self._inject_temp_context(req, followup_prompt):
                        req.prompt = followup_prompt + ("\n\n" + (req.prompt or ""))
                    logger.info(f"{LOG} 成功注入 30 分钟会话追问记忆")
            return

        # 开始处理新视频
        ref = pending["ref"]
        job = ActiveJob(started=time.monotonic(), stage="拉取数据", bvid=ref)
        self._active_jobs[umo] = job

        # 发送先行回执 (ACK)
        if self.cfg.ack_enabled:
            ack_msg = self.cfg.ack_template
            try:
                await event.send(MessageChain([Plain(ack_msg)]))
            except Exception:
                pass

        try:
            async with self._semaphore:
                digest, bundle = await self._process_video_pipeline(job, ref, event)

            if digest and bundle:
                # 存入 30 分钟会话记忆 (供后续精准追问使用)
                self._session_store.put(umo, bundle, digest)
                # 客观结构化呈现：以「临时附加内容」方式注入，不替换用户原始发言，
                # 也不会写入会话历史（官方推荐做法）。
                report = (
                    f"【视频《{bundle.meta.title}》客观结构化分析报告】\n\n"
                    f"{digest}\n\n"
                    "请将上述提炼内容清晰、完整、干练地交付给用户。保持工具属性的严谨与客观，不输出多余的客套、闲聊或口水话。"
                )
                if not self._inject_temp_context(req, report):
                    req.prompt = report
                # 本次请求已把视频看完了，摘掉工具避免主模型重复抓一遍
                self._suppress_watch_tool(req)
            else:
                note = "（视频内容获取失败，可能触发网络或接口风控，请简要客观地向用户说明）"
                if not self._inject_temp_context(req, note):
                    req.prompt = note
        except Exception as exc:
            logger.error(f"{LOG} 视频全流程处理异常: {exc}", exc_info=True)
            note = "（这次获取内容遇到了点小问题，请稍后再试）"
            if not self._inject_temp_context(req, note):
                req.prompt = note + "\n" + (req.prompt or "")
        finally:
            self._active_jobs.pop(umo, None)

    @staticmethod
    def _inject_temp_context(req, text: str) -> bool:
        """把文本作为「仅本次请求可见」的附加用户内容注入。

        AstrBot 4.x 推荐用 `req.extra_user_content_parts` + `mark_as_temp()` 追加，
        既保留用户原始发言，也不会写进会话历史；旧版本不支持时返回 False，
        由调用方回退到修改 `req.prompt`。
        """
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            return False
        try:
            part = TextPart(text=text)
            if hasattr(part, "mark_as_temp"):
                part = part.mark_as_temp()
            parts.append(part)
            return True
        except Exception as exc:
            logger.warning(f"{LOG} 注入临时上下文失败，回退为 prompt 方式: {exc}")
            return False

    @staticmethod
    def _suppress_watch_tool(req) -> None:
        """从本次 Provider 请求的工具集中移除 bilibili_watch，防止同一视频被处理两次。"""
        tools = getattr(req, "func_tool", None)
        try:
            if tools is not None and hasattr(tools, "remove_tool"):
                tools.remove_tool("bilibili_watch")
                return
            if isinstance(tools, list):
                tools[:] = [t for t in tools if getattr(t, "name", "") != "bilibili_watch"]
        except Exception as exc:
            logger.warning(f"{LOG} 移除重复 llm tool 失败(不影响主流程): {exc}")

    async def _process_video_pipeline(
        self, job: ActiveJob, ref: str, event: AstrMessageEvent
    ) -> tuple[str, CleanedVideoBundle]:
        """端到端核心处理流水线：拉取 -> ASR补充 -> 智能清洗 -> 门控决策 -> 子代理提炼。"""
        client = self._get_bili_client()

        # 1. 抓取元数据
        job.stage = "抓取视频详情"
        meta = await self._run_in_pool(client.get_video_meta, ref)
        job.bvid = meta.bvid
        job.title = meta.title

        # 2. 抓取弹幕与评论
        job.stage = "抓取弹幕与评论"
        danmaku_list, comments = await asyncio.gather(
            self._run_in_pool(client.get_danmaku, meta.cid),
            self._run_in_pool(client.get_comments, meta.aid, 15),
        )

        # 3. 尝试抓取官方字幕
        job.stage = "提取字幕"
        subs = await self._run_in_pool(client.get_subtitles, meta.bvid, meta.cid)

        # 4. ASR 智能介入 (当没有字幕，且配置了 ASR 引擎时)
        asr_engine = self._get_asr_engine()
        if not subs and asr_engine and meta.duration <= 900:
            job.stage = "音频抽轨与ASR转录"
            logger.info(f"{LOG} 无官方字幕，启动 ASR 语音识别流水线...")
            try:
                _v_url, a_url = await self._run_in_pool(client.get_stream_urls, meta.bvid, meta.cid)
                if a_url:
                    tmp_mp3 = PLUGIN_DATA_DIR / "cache" / f"asr_{meta.bvid}.mp3"
                    ok = await self._run_in_pool(
                        self._media_extractor.extract_asr_audio, a_url, tmp_mp3, min(meta.duration, 300)
                    )
                    if ok:
                        asr_res = await self._run_in_pool(
                            asr_engine.transcribe, tmp_mp3.read_bytes(), "mp3", "auto"
                        )
                        if asr_res.text:
                            subs = convert_asr_text_to_subtitles(asr_res.text, float(meta.duration))
                            logger.info(f"{LOG} ASR 成功转录 {len(subs)} 句台词！")
                    if tmp_mp3.exists():
                        tmp_mp3.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"{LOG} ASR 转录失败，平滑降级: {e}")

        # 5. 核心清洗流水线
        job.stage = "智能降噪与清洗"
        bundle = run_clean_pipeline(meta, subs, comments, danmaku_list)

        # 6. 门控路由裁决
        plan = self._router.decide(bundle)
        logger.info(f"{LOG} 门控决策完成 -> 模式: {plan.mode.value} | 理由: {plan.reason}")

        # 7. 根据门控决定是否截帧与去重
        frame_paths: list[str] = []
        if plan.need_frames:
            job.stage = "关键帧截图与感知去重"
            v_url, _ = await self._run_in_pool(client.get_stream_urls, meta.bvid, meta.cid)
            if v_url:
                out_dir = PLUGIN_DATA_DIR / "frames" / meta.bvid
                raw_frames = await self._run_in_pool(
                    self._media_extractor.extract_frames,
                    v_url,
                    plan.target_frame_timestamps,
                    out_dir,
                )
                # dHash 汉明去重
                curated_frames = filter_and_deduplicate_frames(raw_frames, max_hamming_threshold=6)
                frame_paths = [str(p) for p, _ in curated_frames]
                logger.info(f"{LOG} 截帧 {len(raw_frames)} 张，去重后保留 {len(frame_paths)} 张")

        # 8. 唤醒模型进行客观事实与结构化提炼
        job.stage = "提炼结构化分析报告"
        analysis_prompt = build_analysis_input(bundle, plan)
        system_prompt = (
            TUTORIAL_EXTRACTOR_SYSTEM_PROMPT
            if plan.mode == ProcessingMode.TEXT_OUTLINE
            else MEME_EXTRACTOR_SYSTEM_PROMPT
        )

        provider_id = self.cfg.vision_provider_id
        digest = await self._call_subagent(
            analysis_prompt, frame_paths, system_prompt, provider_id, event.unified_msg_origin
        )
        return digest, bundle

    async def _session_provider(self, umo: str = ""):
        """取当前会话正在使用的主模型 Provider；失败返回 None。"""
        try:
            return await self.context.get_using_provider_async(umo=umo or None)
        except Exception as exc:
            logger.warning(f"{LOG} 获取当前会话 Provider 失败: {exc}")
            return None

    async def _call_subagent(
        self, prompt: str, image_paths: list[str], system_prompt: str, provider_id: str, umo: str = ""
    ) -> str:
        """调用子代理提炼客观事实笔记。

        AstrBot 4.x 的 Context.llm_generate 必须显式传入 chat_provider_id：
        未配置 vision_provider_id 时回落到当前会话主模型，主模型不可用时再退回
        Provider.text_chat 直调，避免视频分析因缺少 provider 而整体失败。
        """
        gen_kwargs: dict = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "request_max_retries": 2,
        }
        if image_paths:
            gen_kwargs["image_urls"] = image_paths

        provider = None
        if not provider_id:
            provider = await self._session_provider(umo)
            if provider is None:
                logger.error(f"{LOG} 未配置 vision_provider_id 且当前会话没有可用模型 Provider")
                return "（当前没有可用的对话模型 Provider，视频分析报告无法生成）"
            provider_id = str((getattr(provider, "provider_config", None) or {}).get("id") or "")

        if provider_id:
            try:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, **gen_kwargs)
                return getattr(resp, "completion_text", "") or ""
            except Exception as exc:
                logger.error(f"{LOG} 子代理调用失败(provider={provider_id}): {exc}")

        # 兜底：直接调用当前会话 Provider
        if provider is None:
            provider = await self._session_provider(umo)
        if provider is not None:
            try:
                resp = await provider.text_chat(**gen_kwargs)
                return getattr(resp, "completion_text", "") or ""
            except Exception as exc:
                logger.error(f"{LOG} 兜底子代理调用失败: {exc}")
        return "（子代理观察超时或异常）"

    # -- 指令集 -------------------------------------------------------

    @filter.command("看视频", alias=["bwatch"])
    async def cmd_watch(self, event: AstrMessageEvent):
        """指令触发看视频"""
        text = getattr(event, "message_str", "") or ""
        client = self._get_bili_client()
        ref = await self._run_in_pool(client.resolve_ref, text)
        if not ref.startswith("BV") and not ref.lower().startswith("av"):
            card_url = extract_bilibili_card_url(event)
            if card_url:
                ref = await self._run_in_pool(client.resolve_ref, card_url)

        if not ref.startswith("BV") and not ref.lower().startswith("av"):
            event.should_call_llm(True)
            yield event.plain_result("用法：/看视频 <B站链接或BV号>")
            return

        if not self._quota_ok(event):
            event.should_call_llm(True)
            yield event.plain_result("今天的观看次数已用完啦！")
            return

        self._pending_triggers[event.unified_msg_origin] = {"ref": ref}

    @filter.command("看主页", alias=["bup"])
    async def cmd_up(self, event: AstrMessageEvent):
        """查询 UP 主空间信息与近期投稿"""
        text = getattr(event, "message_str", "") or ""
        parts = text.split(maxsplit=1)
        query = parts[1].strip() if len(parts) > 1 else ""
        if not query:
            event.should_call_llm(True)
            yield event.plain_result("用法：/看主页 <UID / 空间链接>")
            return

        uid_str = normalize_up_query(query)
        if not uid_str.isdigit():
            event.should_call_llm(True)
            yield event.plain_result("请输入有效的纯数字 UID 或 B 站空间链接～")
            return

        client = self._get_bili_client()
        try:
            profile = await self._run_in_pool(fetch_up_profile, client, int(uid_str))
            lines = [
                f"【UP 主：{profile.name}】",
                f"• 等级: LV{profile.level} | 粉丝数: {profile.fans} | 投稿数: {profile.archive_count}",
                f"• 签名: {profile.sign or '（无个性签名）'}",
            ]
            if profile.recent_archives:
                lines.append("• 近期投稿:")
                for arc in profile.recent_archives:
                    lines.append(f"  - {arc}")
            yield event.plain_result("\n".join(lines))
        except Exception as exc:
            logger.warning(f"{LOG} 查询 UP 主页失败: {exc}")
            yield event.plain_result("查询 UP 主页失败，可能接口风控或用户不存在。")

    @filter.command("bili登录", alias=["bili_login"])
    async def cmd_login(self, event: AstrMessageEvent):
        """[管理员] 申请 B 站扫码登录二维码"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以操作扫码登录哦～")
            return

        try:
            session = await self._run_in_pool(
                self._qr_service.request_qrcode, PLUGIN_DATA_DIR / "qrcode"
            )
        except Exception as exc:
            yield event.plain_result(f"申请登录二维码失败：{exc}")
            return

        msg_chain: list = [
            Plain("请使用【哔哩哔哩手机客户端】扫描下方二维码登录（180秒内有效）：\n")
        ]
        if session.qr_image_path:
            msg_chain.append(Image.fromFileSystem(str(session.qr_image_path)))
        else:
            msg_chain.append(Plain("（本地未安装 qrcode 库，无法渲染二维码图片）\n"))
        msg_chain.append(Plain(f"\n若图片无法显示，请直接在手机浏览器打开：\n{session.url}"))
        await event.send(MessageChain(msg_chain))

        # 启动后台异步轮询任务
        asyncio.create_task(self._poll_login_task(event, session.qrcode_key))

    async def _poll_login_task(self, event: AstrMessageEvent, qrcode_key: str):
        """后台轮询扫码状态任务"""
        for _ in range(60):  # 60 次 * 3秒 = 180秒
            await asyncio.sleep(3)
            try:
                res = await self._run_in_pool(self._qr_service.poll_status, qrcode_key)
                if res.code == 0 and res.credentials:
                    # 登录成功！存盘并热重载
                    self._cred_manager.save(res.credentials)
                    # 验证并读取登录昵称
                    client = self._get_bili_client()
                    meta = await self._run_in_pool(client.get_json, "https://api.bilibili.com/x/web-interface/nav")
                    uname = (meta.get("data") or {}).get("uname", "B站用户")
                    uid = (meta.get("data") or {}).get("mid", "")
                    await event.send(
                        MessageChain([Plain(f"🎉 扫码登录成功！\n已成功绑定账号：{uname} (UID: {uid})\n1080P 高画质已解锁！")])
                    )
                    return
                elif res.code == 86038:  # 超时
                    await event.send(MessageChain([Plain("二维码已过期，请重新发送 /bili登录 申请。")]))
                    return
            except Exception as exc:
                logger.warning(f"{LOG} 轮询登录异常: {exc}")
                break

    @filter.command("bili注销", alias=["bili_logout"])
    async def cmd_logout(self, event: AstrMessageEvent):
        """[管理员] 清除本地保存的扫码登录凭据"""
        if not self._is_admin(event):
            yield event.plain_result("只有管理员可以操作注销哦～")
            return
        self._cred_manager.clear()
        yield event.plain_result("已成功注销并清除本地凭证，系统已自动回退到游客态或默认配置。")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        is_admin = getattr(event, "is_admin", False)
        return bool(is_admin() if callable(is_admin) else is_admin)

    @filter.command("bili状态", alias=["bili_status"])
    async def cmd_status(self, event: AstrMessageEvent):
        """检查登录凭据、画质与 ASR 状态"""
        has_qr_cred = self._cred_manager.has_valid_credential()
        sessdata = self._cred_manager.get_sessdata() or self.cfg.sessdata
        asr_prov = self.cfg.asr_provider

        status_text = "未登录 (游客态 360P/480P)"
        if has_qr_cred:
            status_text = "已登录 [扫码凭据，解锁 1080P 高清]"
        elif sessdata:
            status_text = "已登录 [来自独立配置/环境变量，解锁 1080P 高清]"

        lines = [
            "【B站之眼evolution 系统状态】",
            f"• 登录状态: {status_text}",
            f"• ASR 语音识别: {asr_prov.upper() if asr_prov != 'none' else '已禁用'}",
            f"• FFmpeg 状态: {self._media_extractor.get_ffmpeg_exe()}",
        ]
        yield event.plain_result("\n".join(lines))

    # -- LLM Tools ---------------------------------------------------

    @filter.llm_tool(name="bilibili_watch")
    async def tool_watch(self, event: AstrMessageEvent, bvid: str):
        """看一个 B 站视频，返回你刚才观看后看到的客观内容。

        Args:
            bvid(string): B站 BV 号、av 号或视频链接
        """
        client = self._get_bili_client()
        ref = await self._run_in_pool(client.resolve_ref, bvid)
        if not ref.startswith("BV") and not ref.lower().startswith("av"):
            return "未识别到有效的 B 站视频标识。"

        job = ActiveJob(started=time.monotonic(), stage="工具处理中", bvid=ref)
        self._active_jobs[event.unified_msg_origin] = job
        try:
            async with self._semaphore:
                digest, bundle = await self._process_video_pipeline(job, ref, event)
            if digest and bundle:
                self._session_store.put(event.unified_msg_origin, bundle, digest)
                return f"视频《{bundle.meta.title}》的客观结构化提炼报告如下：\n{digest}"
            return "视频处理失败，可能网络或接口风控，请稍后再试。"
        except Exception as exc:
            return f"看视频失败：{exc}"
        finally:
            self._active_jobs.pop(event.unified_msg_origin, None)
