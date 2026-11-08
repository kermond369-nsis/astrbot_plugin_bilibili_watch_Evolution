"""统一配置管理层（Config Provider）。

支持分层优先级读取，彻底解耦硬编码：
优先级：AstrBot WebUI 传入配置 > 本地 config.json (Docker挂载/独立配置) > 环境变量 (os.environ)
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_FILE_NAME = "config.json"


@dataclass
class AppConfig:
    # B 站凭证
    sessdata: str = ""

    # ASR 语音识别配置
    asr_provider: str = "none"  # "none" | "mimo" | "siliconflow"
    mimo_api_key: str = ""
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    siliconflow_api_key: str = ""
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    siliconflow_model: str = "FunAudioLLM/SenseVoiceSmall"
    asr_max_seconds: int = 1800      # 单视频最多转录多少秒
    asr_chunk_seconds: int = 300     # 单块音频长度，避免超过 STT 大小限制

    # 视觉子代理配置
    vision_provider_id: str = ""

    # 游戏实况判定：auto | on | off
    gameplay_mode: str = "auto"

    # 画面证据
    frame_budget_cap: int = 36       # 场景联表最多多少帧
    hud_sheet_max: int = 6           # 操作栏时间带最多几张
    keep_frame_dirs: int = 6         # 保留最近多少个视频的帧缓存
    transcript_ttl_hours: int = 72   # 字幕/ASR 原文缓存保留小时数

    # FFmpeg 路径
    ffmpeg_path: str = ""

    # 频控与权限
    user_daily_quota: int = 20
    quota_exempt_admins: bool = True
    cooldown_seconds: int = 15

    # 交互文案
    ack_enabled: bool = True
    ack_template: str = "我去看看这个视频，稍等一下～"
    wait_notice_enabled: bool = True

    @classmethod
    def load(cls, plugin_dir: Path, astrbot_config: dict[str, Any] | None = None) -> AppConfig:
        """多层级自适应加载配置：

        1. 基础默认值
        2. 读取本地/Docker挂载的 config.json (如果存在)
        3. 读取环境变量
        4. 读取 AstrBot WebUI 传入的动态配置 (最高优先级)
        """
        data: dict[str, Any] = {}

        # 1. 尝试从独立 config.json 文件读取 (便于 Docker Volume 挂载)
        cfg_path = plugin_dir / CONFIG_FILE_NAME
        if cfg_path.exists():
            try:
                file_content = cfg_path.read_text(encoding="utf-8")
                data.update(json.loads(file_content))
            except Exception:
                pass

        # 2. 从环境变量中覆盖 (便于 Docker -e 环境变量注入)
        env_mappings = {
            "BILI_SESSDATA": "sessdata",
            "MIMO_API_KEY": "mimo_api_key",
            "SILICONFLOW_API_KEY": "siliconflow_api_key",
            "ASR_PROVIDER": "asr_provider",
            "FFMPEG_PATH": "ffmpeg_path",
            "VISION_PROVIDER_ID": "vision_provider_id",
        }
        for env_key, conf_key in env_mappings.items():
            val = os.environ.get(env_key)
            if val:
                data[conf_key] = val.strip()

        # 3. AstrBot WebUI 配置面板覆盖 (最高优先级)
        if astrbot_config and isinstance(astrbot_config, dict):
            for k, v in astrbot_config.items():
                if v is not None and v != "":
                    data[k] = v

        # 组装强类型对象
        return cls(
            sessdata=str(data.get("sessdata", "")).strip(),
            asr_provider=str(data.get("asr_provider", "none")).strip().lower(),
            mimo_api_key=str(data.get("mimo_api_key", "")).strip(),
            mimo_base_url=str(data.get("mimo_base_url", "https://api.xiaomimimo.com/v1")).strip(),
            siliconflow_api_key=str(data.get("siliconflow_api_key", "")).strip(),
            siliconflow_base_url=str(data.get("siliconflow_base_url", "https://api.siliconflow.cn/v1")).strip(),
            siliconflow_model=str(data.get("siliconflow_model", "FunAudioLLM/SenseVoiceSmall")).strip(),
            asr_max_seconds=int(data.get("asr_max_seconds", 1800)),
            asr_chunk_seconds=int(data.get("asr_chunk_seconds", 300)),
            vision_provider_id=str(data.get("vision_provider_id", "")).strip(),
            gameplay_mode=str(data.get("gameplay_mode", "auto")).strip().lower(),
            frame_budget_cap=int(data.get("frame_budget_cap", 36)),
            hud_sheet_max=int(data.get("hud_sheet_max", 6)),
            keep_frame_dirs=int(data.get("keep_frame_dirs", 6)),
            transcript_ttl_hours=int(data.get("transcript_ttl_hours", 72)),
            ffmpeg_path=str(data.get("ffmpeg_path", "")).strip(),
            user_daily_quota=int(data.get("user_daily_quota", 20)),
            quota_exempt_admins=bool(data.get("quota_exempt_admins", True)),
            cooldown_seconds=int(data.get("cooldown_seconds", 15)),
            ack_enabled=bool(data.get("ack_enabled", True)),
            ack_template=str(data.get("ack_template", "我去看看这个视频，稍等一下～")),
            wait_notice_enabled=bool(data.get("wait_notice_enabled", True)),
        )
