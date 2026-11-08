"""astrbot_plugin_bilibili_watch

智能清洗与双模门控的 B 站多模态感知插件。
子代理（眼睛）在隔离上下文提炼客观事实，主模型（脑子）以自身人设与语气自然发声。
"""

from __future__ import annotations

import asyncio
import base64
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
from core.cleaner.pipeline import run_clean_pipeline
from core.client.bili_client import BiliClient
from core.client.card_extractor import extract_bilibili_card_url
from core.client.media_extractor import MediaExtractor
from core.client.up_extractor import fetch_up_profile, normalize_up_query
from core.config import AppConfig
from core.gating.router import GatingRouter
from core.media.frame_plan import hud_interval, select_scene_times
from core.media.sampler import (
    Sample,
    analyze_changes,
    cleanup_old_frames,
    compose_contact_sheet,
    compose_hud_sheet,
    sample_stream,
)
from core.models import (
    CleanedVideoBundle,
    GatingPlan,
    ProcessingMode,
    RawSubtitleItem,
    SubtitleTrack,
)
from core.perception.evidence import assess_subtitle_trust
from core.perception.prompt_builder import (
    EvidenceBundle,
    SheetInfo,
    build_analysis_input,
    pick_system_prompt,
)
from core.perception.session_store import SessionStore
from core.perception.transcript_store import (
    TranscriptLine,
    TranscriptRecord,
    TranscriptStore,
)
from core.utils.timecode import format_time_point, parse_time_point
from core.web.api import PluginWebAPI

PLUGIN_NAME = "astrbot_plugin_bilibili_watch"
PLUGIN_VERSION = "1.4.0"  # 与 metadata.yaml 保持一致
LOG = "[预知眼]"

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
        # 保留 AstrBot 的配置对象（dict 子类，带 save_config_async），设置页保存时用
        self._plugin_config = config if isinstance(config, dict) else None
        # 统一分层配置读取：WebUI 传入 > 本地 config.json > 环境变量
        self.cfg = AppConfig.load(PLUGIN_DIR, config)
        PLUGIN_DATA_DIR.mkdir(parents=True, exist_ok=True)
        self._cred_manager = CredentialManager(PLUGIN_DATA_DIR)
        self._qr_service = BiliQrLoginService()
        self._session_store = SessionStore(ttl_seconds=1800)
        self._transcripts = TranscriptStore(
            PLUGIN_DATA_DIR / "transcripts", ttl_hours=self.cfg.transcript_ttl_hours
        )
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

        # 可视化设置页（views/settings + register_web_api）
        self._web_api = PluginWebAPI(
            self,
            plugin_root=PLUGIN_DIR,
            data_dir=PLUGIN_DATA_DIR,
            plugin_name=PLUGIN_NAME,
            version=PLUGIN_VERSION,
        )
        self._web_api.register(context)

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
        """端到端核心处理流水线：拉取 -> 字幕校验/ASR -> 智能清洗 -> 门控 -> 画面证据 -> 子代理提炼。"""
        client = self._get_bili_client()

        # 1. 抓取元数据（含分区与标签）
        job.stage = "抓取视频详情"
        meta = await self._run_in_pool(client.get_video_meta, ref)
        job.bvid = meta.bvid
        job.title = meta.title

        # 2. 并发抓取弹幕、评论、字幕
        job.stage = "抓取弹幕/评论/字幕"
        danmaku_list, comments, track = await asyncio.gather(
            self._run_in_pool(client.get_danmaku, meta.cid),
            self._run_in_pool(client.get_comments, meta.aid, 40),
            self._run_in_pool(client.get_subtitle_track, meta.bvid, meta.cid),
        )

        # 3. 字幕可信度预检：错配字幕或无字幕时，尝试用 ASR 重建口播
        stream_info = None
        trust = assess_subtitle_trust(track, meta, comments)
        if (not track.items) or trust.level == "suspect":
            if self.cfg.asr_provider != "none":
                job.stage = "音轨分块与ASR转录"
                stream_info = await self._run_in_pool(
                    client.get_stream_info, meta.bvid, meta.cid
                )
                asr_track = await self._run_asr(job, meta, stream_info, client.media_headers())
                if asr_track.ok:
                    track = asr_track

        # 4. 核心清洗流水线（内部会再次评估字幕可信度）
        job.stage = "智能降噪与清洗"
        bundle = run_clean_pipeline(
            meta,
            track.items,
            comments,
            danmaku_list,
            subtitle_track=track,
            gameplay_mode=self.cfg.gameplay_mode,
        )
        await self._save_transcript(bundle)

        # 5. 门控路由裁决
        plan = self._router.decide(bundle)
        logger.info(f"{LOG} 门控决策完成 -> 模式: {plan.mode.value} | 理由: {plan.reason}")

        # 6. 画面证据：一次解码同时产出场景联表 + 底栏操作栏时间带
        images: list[str] = []
        evidence: EvidenceBundle | None = None
        if plan.need_frames:
            job.stage = "采样画面证据"
            evidence, images = await self._collect_frame_evidence(
                job, client, meta, plan, stream_info
            )

        # 7. 唤醒模型进行客观事实与结构化提炼
        job.stage = "提炼结构化分析报告"
        analysis_prompt = build_analysis_input(bundle, plan, evidence=evidence)
        system_prompt = pick_system_prompt(plan)

        provider_id = self.cfg.vision_provider_id
        digest = await self._call_subagent(
            analysis_prompt, images, system_prompt, provider_id, event.unified_msg_origin
        )
        return digest, bundle

    async def _run_asr(
        self, job: ActiveJob, meta, stream_info, headers: dict | None = None
    ) -> SubtitleTrack:
        """长音频分块转录：每块独立抽轨 + 独立识别，单块失败不影响其它块。"""
        engine = self._get_asr_engine()
        if engine is None or stream_info is None or not stream_info.audio_url:
            return SubtitleTrack()

        headers = headers or {}
        total = max(0, min(int(meta.duration), int(self.cfg.asr_max_seconds)))
        if total <= 0:
            return SubtitleTrack()
        chunk = max(60, min(int(self.cfg.asr_chunk_seconds), 600))
        items: list[RawSubtitleItem] = []
        failures = 0

        for offset in range(0, total, chunk):
            duration = min(chunk, total - offset)
            cache_file = PLUGIN_DATA_DIR / "cache" / f"asr_{meta.bvid}_{offset}.mp3"
            ok = await self._run_in_pool(
                self._media_extractor.extract_asr_audio,
                stream_info.audio_url,
                cache_file,
                int(duration),
                float(offset),
                headers.get("Referer", "https://www.bilibili.com/"),
                headers.get("User-Agent", ""),
                headers.get("Cookie", ""),
            )
            if not ok:
                failures += 1
                continue
            try:
                asr_res = await self._run_in_pool(
                    engine.transcribe, cache_file.read_bytes(), "mp3", "auto"
                )
                text = getattr(asr_res, "text", "") or ""
            except Exception as exc:
                logger.warning(f"{LOG} ASR 分块 {offset}s 失败: {exc}")
                failures += 1
                text = ""
            finally:
                cache_file.unlink(missing_ok=True)

            if not text:
                continue
            for item in convert_asr_text_to_subtitles(text, float(duration)):
                items.append(
                    RawSubtitleItem(
                        start=round(item.start + offset, 2),
                        end=round(item.end + offset, 2),
                        content=item.content,
                    )
                )

        if not items:
            logger.info(f"{LOG} ASR 未产出可用台词（失败块 {failures}）")
            return SubtitleTrack(error=f"asr_empty:{failures}")
        logger.info(f"{LOG} ASR 转录完成：{len(items)} 句，失败块 {failures}")
        return SubtitleTrack(items=items, source="asr", lang="auto")

    async def _collect_frame_evidence(
        self, job: ActiveJob, client: BiliClient, meta, plan: GatingPlan, stream_info=None
    ) -> tuple[EvidenceBundle | None, list[str]]:
        """采样并编排证据图。返回 (证据索引, 图片路径列表)，图片顺序与索引一致。"""
        if stream_info is None:
            stream_info = await self._run_in_pool(client.get_stream_info, meta.bvid, meta.cid)
        if stream_info is None or not stream_info.ok:
            logger.warning(f"{LOG} 没有可用视频流，跳过画面证据")
            return None, []

        headers = client.media_headers()
        workdir = PLUGIN_DATA_DIR / "frames" / meta.bvid
        hud_gap = (
            hud_interval(meta.duration, plan.hud_max_sheets or self.cfg.hud_sheet_max, 32)
            if plan.need_hud
            else 0.0
        )
        job.stage = "单次解码采样（场景帧 + HUD）"
        sampling = await self._run_in_pool(
            functools.partial(
                sample_stream,
                stream_info.video_url,
                float(meta.duration),
                workdir,
                self._media_extractor.get_ffmpeg_exe(),
                headers.get("Referer", "https://www.bilibili.com/"),
                headers.get("User-Agent", ""),
                headers.get("Cookie", ""),
                None,
                hud_gap,
            )
        )
        if not sampling.ok:
            logger.warning(f"{LOG} 采样失败，回退逐点截帧: {sampling.error}")
            return await self._fallback_frame_evidence(workdir, meta, plan, stream_info, headers)

        job.stage = "编排场景联表"
        thumbs = sampling.thumbs
        changes, _brightness = analyze_changes([s.thumb for s in thumbs])
        selection = select_scene_times(
            [s.time for s in thumbs],
            changes,
            min(plan.target_frame_count or 12, self.cfg.frame_budget_cap),
            meta.duration,
        )
        by_time = {round(s.time, 1): s for s in thumbs}
        chosen = []
        for ts in selection.times:
            sample = by_time.get(round(ts, 1))
            if sample is None and thumbs:
                sample = min(thumbs, key=lambda s: abs(s.time - ts))
            if sample is not None:
                chosen.append(sample)

        images: list[str] = []
        sheets: list[SheetInfo] = []
        for sheet_no, start in enumerate(range(0, len(chosen), 6), start=1):
            group = chosen[start : start + 6]
            path = await self._run_in_pool(
                compose_contact_sheet, group, workdir / f"scene_{sheet_no:02d}.jpg"
            )
            if not path:
                continue
            images.append(str(path))
            sheets.append(
                SheetInfo(
                    index=len(images),
                    kind="scene",
                    start=group[0].time,
                    end=group[-1].time,
                    cells=len(group),
                    sheet_no=sheet_no,
                )
            )

        if plan.need_hud and sampling.huds:
            job.stage = "编排操作栏时间带"
            cell_count = 32
            for sheet_no, start in enumerate(range(0, len(sampling.huds), cell_count), start=1):
                group = sampling.huds[start : start + cell_count]
                path = await self._run_in_pool(
                    compose_hud_sheet, group, workdir / f"hud_{sheet_no:02d}.jpg"
                )
                if not path:
                    continue
                images.append(str(path))
                sheets.append(
                    SheetInfo(
                        index=len(images),
                        kind="hud",
                        start=group[0].time,
                        end=group[-1].time,
                        cells=len(group),
                        interval=sampling.hud_interval,
                        sheet_no=sheet_no,
                    )
                )

        cleanup_old_frames(
            PLUGIN_DATA_DIR / "frames", meta.bvid, max_dirs=max(2, self.cfg.keep_frame_dirs)
        )
        if not images:
            return None, []
        return (
            EvidenceBundle(
                sheets=sheets,
                frame_times=[s.time for s in chosen],
                hud_interval=sampling.hud_interval,
                sample_note=(
                    f"缩略图 {sampling.thumb_count} 张（{sampling.thumb_fps:.2f} fps），"
                    f"HUD 裁片 {sampling.hud_count} 张；图像均为原始截取，未做内容修改。"
                ),
            ),
            images,
        )

    async def _fallback_frame_evidence(
        self, workdir: Path, meta, plan: GatingPlan, stream_info, headers: dict
    ) -> tuple[EvidenceBundle | None, list[str]]:
        """单次解码不可用时的兜底：按门控时间点逐张截帧，再拼一张联表。"""
        timestamps = [t for t in plan.target_frame_timestamps[:12] if 0 < t < meta.duration]
        if not timestamps:
            return None, []
        out_dir = workdir / "fallback"
        frames = await self._run_in_pool(
            self._media_extractor.extract_frames,
            stream_info.video_url,
            timestamps,
            out_dir,
            720,
            headers.get("Referer", "https://www.bilibili.com/"),
            headers.get("User-Agent", ""),
        )
        if not frames:
            return None, []
        samples = [
            Sample(index=i + 1, time=ts, thumb=path) for i, (path, ts) in enumerate(frames)
        ]
        path = await self._run_in_pool(
            compose_contact_sheet, samples, workdir / "scene_fallback.jpg"
        )
        if not path:
            return None, []
        return (
            EvidenceBundle(
                sheets=[SheetInfo(index=1, kind="scene", start=samples[0].time, end=samples[-1].time, cells=len(samples))],
                frame_times=[s.time for s in samples],
                hud_interval=0.0,
                sample_note="逐点截帧兜底（未启用单次解码采样）",
            ),
            [str(path)],
        )

    async def _global_provider(self):
        """取 AstrBot 全局默认对话模型（不带 umo，避免被会话隔离覆盖）。"""
        try:
            return await self.context.get_using_provider_async()
        except Exception as exc:
            logger.warning(f"{LOG} 获取全局默认 Provider 失败: {exc}")
            return None

    async def _session_provider(self, umo: str = ""):
        """取当前会话正在使用的主模型 Provider；失败返回 None。"""
        try:
            return await self.context.get_using_provider_async(umo=umo or None)
        except Exception as exc:
            logger.warning(f"{LOG} 获取当前会话 Provider 失败: {exc}")
            return None

    @staticmethod
    def _provider_id(provider) -> str:
        return str((getattr(provider, "provider_config", None) or {}).get("id") or "")

    async def _call_subagent(
        self, prompt: str, image_paths: list[str], system_prompt: str, provider_id: str, umo: str = ""
    ) -> str:
        """调用子代理提炼客观事实笔记。

        未配置 vision_provider_id 时，跟随 **AstrBot 全局默认对话模型**
        （`context.get_using_provider_async()` 不带 umo，避免会话隔离影响）；
        全局模型不可用时才回退当前会话模型，再回退 Provider.text_chat。
        """
        gen_kwargs: dict = {
            "prompt": prompt,
            "system_prompt": system_prompt,
            "request_max_retries": 2,
        }
        if image_paths:
            gen_kwargs["image_urls"] = image_paths

        provider = None
        if provider_id:
            logger.info(f"{LOG} 视觉子代理：使用指定 Provider {provider_id}")
        else:
            provider = await self._global_provider()
            if provider is None:
                provider = await self._session_provider(umo)
            if provider is None:
                logger.error(f"{LOG} 未配置 vision_provider_id，且 AstrBot 没有可用的全局/会话对话模型")
                return "（当前没有可用的对话模型 Provider，视频分析报告无法生成）"
            provider_id = self._provider_id(provider)
            logger.info(
                f"{LOG} 视觉子代理：跟随 AstrBot 全局默认模型 {provider_id or provider.meta().id}"
            )

        # 证据图较多时逐级降级：全部图 -> 前 4 张 -> 纯文本，避免一次超限整体失败
        attempts: list[list[str]] = []
        if image_paths:
            attempts.append(list(image_paths))
            if len(image_paths) > 4:
                attempts.append(list(image_paths[:4]))
        attempts.append([])

        last_error = ""
        for image_set in attempts:
            call_kwargs = dict(gen_kwargs)
            call_kwargs.pop("image_urls", None)
            if image_set:
                call_kwargs["image_urls"] = image_set
            if provider_id:
                try:
                    resp = await self.context.llm_generate(chat_provider_id=provider_id, **call_kwargs)
                    return getattr(resp, "completion_text", "") or ""
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        f"{LOG} 子代理调用失败(provider={provider_id}, images={len(image_set)}): {exc}"
                    )
            if provider is None:
                provider = await self._global_provider() or await self._session_provider(umo)
            if provider is not None:
                try:
                    resp = await provider.text_chat(**call_kwargs)
                    return getattr(resp, "completion_text", "") or ""
                except Exception as exc:
                    last_error = str(exc)
                    logger.warning(
                        f"{LOG} 兜底子代理调用失败(images={len(image_set)}): {exc}"
                    )
        logger.error(f"{LOG} 子代理全部重试均失败: {last_error}")
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
            "【逐渐完善的预知眼 系统状态】",
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

    # -- 原文缓存与二次查询 -------------------------------------------

    async def _save_transcript(self, bundle: CleanedVideoBundle) -> None:
        """把字幕/ASR 原文落盘，供 bilibili_transcript 直接检索。"""
        track = bundle.subtitle_track
        if track is None or not track.items:
            return
        trust = bundle.subtitle_trust.level if bundle.subtitle_trust else "none"
        record = TranscriptRecord(
            bvid=bundle.meta.bvid,
            title=bundle.meta.title,
            source=track.source,
            trust=trust,
            duration=float(bundle.meta.duration),
            lines=[
                TranscriptLine(start=item.start, end=item.end, text=item.content)
                for item in track.items
            ],
        )
        try:
            await self._run_in_pool(self._transcripts.save, record)
        except Exception as exc:
            logger.warning(f"{LOG} 原文缓存写入失败(不影响主流程): {exc}")

    def _last_video_bvid(self, umo: str) -> str:
        record = self._session_store.get(umo)
        return record.bundle.meta.bvid if record else ""

    @filter.llm_tool(name="bilibili_transcript")
    async def tool_transcript(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        start: str = "",
        end: str = "",
        bvid: str = "",
        limit: int = 20,
    ):
        """查询最近看过视频的字幕/ASR 原文，不重新看视频。需要原话、台词、具体时间点时使用。

        Args:
            keyword(string): 关键词，留空表示不限
            start(string): 起始时间，秒数或 mm:ss / hh:mm:ss，留空从头
            end(string): 结束时间，格式同上，留空到结尾
            bvid(string): 可选；不填则用当前会话最近看过的视频
            limit(int): 最多返回多少句，默认 20
        """
        target = (bvid or "").strip() or self._last_video_bvid(event.unified_msg_origin)
        if not target:
            return "最近没有看过视频；先让我看一个视频，或直接提供 BV 号。"
        record = await self._run_in_pool(self._transcripts.get, target)
        if record is None:
            return (
                f"没有 {target} 的原文缓存（可能过期，或该视频没有可用字幕/ASR）。"
                "可以重新让我看一次视频来生成缓存。"
            )
        if record.trust == "suspect":
            return (
                f"{target} 的字幕被判定为疑似错配，已不进入分析材料，因此不提供原文。"
                "建议配置 ASR 后重新观看，用音频转录替代。"
            )
        start_s = parse_time_point(start) if start else 0.0
        end_s = parse_time_point(end) if end else None
        if start and start_s is None:
            return "start 格式不对，请用秒数、mm:ss 或 hh:mm:ss。"
        if end and end_s is None:
            return "end 格式不对，请用秒数、mm:ss 或 hh:mm:ss。"
        hits = await self._run_in_pool(
            self._transcripts.search,
            target,
            keyword,
            start_s or 0.0,
            end_s,
            max(1, min(int(limit or 20), 60)),
        )
        if not hits:
            return "这个范围内没有匹配的原文；可以换关键词，或放宽时间范围。"
        source = {"official": "官方字幕", "ai": "B站AI字幕", "asr": "ASR转录"}.get(
            record.source, record.source
        )
        lines = [f"【{record.title or target}｜{source}｜共 {len(hits)} 条命中】"]
        for line in hits:
            lines.append(f"[{format_time_point(line.start)}] {line.text}")
        return "\n".join(lines)

    @filter.llm_tool(name="bilibili_frame")
    async def tool_frame(self, event: AstrMessageEvent, time_point: str, bvid: str = ""):
        """查看最近看过视频的某一帧画面，返回整帧 + 底栏 UI 裁片；用户要看某个时间点的画面时使用。

        Args:
            time_point(string): 时间点，秒数或 mm:ss / hh:mm:ss
            bvid(string): 可选；不填则用当前会话最近看过的视频
        """
        try:
            from mcp.types import CallToolResult, ImageContent, TextContent
        except Exception:
            return "当前运行环境缺少 mcp.types，无法返回图片。"

        def text_result(text: str):
            return CallToolResult(content=[TextContent(type="text", text=text)])

        umo = event.unified_msg_origin
        record = self._session_store.get(umo)
        if record is None:
            return text_result("最近没有看过的视频；请先让我看一个视频再截图。")
        meta = record.bundle.meta
        wanted = (bvid or "").strip()
        if wanted:
            ref = await self._run_in_pool(self._get_bili_client().resolve_ref, wanted)
            if ref and ref.upper() != meta.bvid.upper():
                return text_result(f"最近看的是 {meta.bvid}，不是 {wanted}；请先看目标视频。")
        target = parse_time_point(time_point)
        if target is None:
            return text_result("时间点格式不对，请用秒数、mm:ss 或 hh:mm:ss。")
        if meta.duration and target > meta.duration:
            return text_result(f"这个视频只有 {meta.duration} 秒，没有 {time_point} 这个时间点。")

        client = self._get_bili_client()
        out_dir = PLUGIN_DATA_DIR / "frames" / meta.bvid / "requested"
        try:
            info = await self._run_in_pool(client.get_stream_info, meta.bvid, meta.cid)
            if not info.ok:
                return text_result("这次没拿到视频流（可能网络/风控），稍后再试。")
            headers = client.media_headers()
            pairs = await self._run_in_pool(
                self._media_extractor.extract_frames,
                info.video_url,
                [target],
                out_dir,
                1080,
                headers.get("Referer", ""),
                headers.get("User-Agent", ""),
                headers.get("Cookie", ""),
            )
        except Exception as exc:
            logger.warning(f"{LOG} 按时间点截帧失败: {exc}")
            return text_result("这次没截到图（可能网络/风控），稍后再试。")
        if not pairs:
            return text_result("这个时间点没有截到画面，稍后再试。")

        frame_path, actual = pairs[0]
        payloads = [("整帧画面", frame_path)]
        hud_path = self._crop_hud(frame_path)
        if hud_path:
            payloads.append(("底栏 UI 裁片（费用/技能卡/血条）", hud_path))

        try:
            root = PLUGIN_DATA_DIR.resolve()
            content = [
                TextContent(
                    type="text",
                    text=f"{meta.bvid} 在 {format_time_point(actual)} 附近的画面（原片 {format_time_point(target)}）。",
                )
            ]
            for label, path in payloads:
                data_path = Path(path).resolve()
                if not data_path.is_relative_to(root):
                    continue
                payload = base64.b64encode(data_path.read_bytes()).decode("ascii")
                mime = "image/png" if data_path.suffix.lower() == ".png" else "image/jpeg"
                content.append(TextContent(type="text", text=label))
                content.append(ImageContent(type="image", data=payload, mimeType=mime))
        except Exception as exc:
            logger.warning(f"{LOG} 读取截图失败: {exc}")
            return text_result("读取截图失败，稍后再试。")
        return CallToolResult(content=content)

    @staticmethod
    def _crop_hud(frame_path: Path) -> Path | None:
        """从整帧里裁出底栏 UI，并适度放大，便于模型读费用/卡片。

        先用 64x36 灰度估计画面有效区域，避开上下黑边；再取有效区域右下角。
        """
        try:
            from PIL import Image

            with Image.open(frame_path) as img:
                width, height = img.size
                small = img.convert("L").resize((64, 36))
                pixels = list(small.getdata())
                content_rows = []
                for row in range(36):
                    values = pixels[row * 64 : (row + 1) * 64]
                    mean = sum(values) / 64
                    var = sum((v - mean) ** 2 for v in values) / 64
                    # 画面内容行：有亮度，或有明显的行内变化；纯黑边两者都低
                    if mean > 12 or var ** 0.5 > 6:
                        content_rows.append(row)
                top = content_rows[0] if content_rows else 0
                bottom = content_rows[-1] + 1 if content_rows else 36
                content_top = int(height * top / 36)
                content_bottom = int(height * bottom / 36)
                content_height = max(1, content_bottom - content_top)
                box = (
                    int(width * 0.50),
                    max(content_top, content_bottom - int(content_height * 0.20)),
                    width,
                    content_bottom,
                )
                crop = img.crop(box)
                crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
                out = Path(frame_path).with_name(Path(frame_path).stem + "_hud.jpg")
                crop.convert("RGB").save(out, quality=90)
                return out
        except Exception:
            return None
