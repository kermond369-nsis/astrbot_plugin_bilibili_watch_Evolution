"""音视频流提取与轻量截取引擎（Media Extractor）。

负责：
1. 探测系统环境 ffmpeg（优先支持系统 PATH，同时支持 imageio-ffmpeg 自动管理与自定义路径）；
2. 轻量音频流抽轨（用于 ASR）：直接从 Dash 音频流提取 16kHz mono 低码率 mp3，10 分钟音频压缩至 1~2MB；
3. 关键帧截图（用于视觉子代理）：精准基于秒数截取 480P 画面。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from astrbot.api import logger


class MediaExtractor:
    def __init__(self, custom_ffmpeg_path: str = ""):
        self._custom_ffmpeg = custom_ffmpeg_path.strip()
        self._cached_ffmpeg: str | None = None

    def get_ffmpeg_exe(self) -> str:
        """获取可用的 ffmpeg 可执行文件路径。

        探测优先级：
        1. 自定义指定路径；
        2. 系统 PATH 中的 ffmpeg；
        3. imageio-ffmpeg 自包含二进制包。
        """
        if self._cached_ffmpeg:
            return self._cached_ffmpeg

        if self._custom_ffmpeg and Path(self._custom_ffmpeg).exists():
            self._cached_ffmpeg = self._custom_ffmpeg
            return self._cached_ffmpeg

        # 检查系统 PATH
        import shutil

        sys_path = shutil.which("ffmpeg")
        if sys_path:
            self._cached_ffmpeg = sys_path
            return self._cached_ffmpeg

        # 检查 imageio_ffmpeg 自包含二进制
        try:
            import imageio_ffmpeg

            self._cached_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            return self._cached_ffmpeg
        except Exception:
            pass

        # 兜底
        self._cached_ffmpeg = "ffmpeg"
        return self._cached_ffmpeg

    @staticmethod
    def _is_allowed_stream_url(url: str) -> bool:
        """仅接受 B 站 playurl 接口下发的 http(s) 流地址。

        ffmpeg 以参数列表调用（不经 shell），此处再做一层协议白名单，
        避免把任意字符串当作输入源。
        """
        return isinstance(url, str) and url.startswith(("http://", "https://"))

    def extract_asr_audio(
        self,
        audio_stream_url: str,
        output_mp3_path: Path,
        max_duration_seconds: int = 600,
        referer: str = "https://www.bilibili.com/",
        user_agent: str = "",
    ) -> bool:
        """从 B 站 Dash 音频流中提取经过极致压缩的轻量音频（供 ASR 上传使用）。

        规格：单声道 (mono 1)、16kHz 采样率、32kbps 码率。
        5 分钟音频仅约 1.2MB，10 分钟音频约 2.4MB，远在 10MB 限制内。
        """
        if not self._is_allowed_stream_url(audio_stream_url):
            logger.warning("拒绝非 http(s) 的音频流地址，跳过抽轨")
            return False
        output_mp3_path.parent.mkdir(parents=True, exist_ok=True)
        headers = f"Referer: {referer}\r\n"
        if user_agent:
            headers += f"User-Agent: {user_agent}\r\n"

        cmd = [
            self.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-headers",
            headers,
            "-i",
            audio_stream_url,
            "-t",
            str(max_duration_seconds),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "32k",
            "-y",
            str(output_mp3_path),
        ]

        try:
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
            return output_mp3_path.exists() and output_mp3_path.stat().st_size > 0
        except Exception as exc:
            logger.error(f"提取 ASR 音频流失败: {exc}")
            return False

    def extract_frames(
        self,
        video_stream_url: str,
        timestamps: Sequence[float],
        out_dir: Path,
        height: int = 480,
        referer: str = "https://www.bilibili.com/",
        user_agent: str = "",
    ) -> list[tuple[Path, float]]:
        """截取多个关键时间点的单帧画面"""
        if not self._is_allowed_stream_url(video_stream_url):
            logger.warning("拒绝非 http(s) 的视频流地址，跳过截帧")
            return []
        out_dir.mkdir(parents=True, exist_ok=True)
        headers = f"Referer: {referer}\r\n"
        if user_agent:
            headers += f"User-Agent: {user_agent}\r\n"

        extracted: list[tuple[Path, float]] = []

        for index, seconds in enumerate(timestamps, start=1):
            out_file = out_dir / f"frame_{index:02d}_{int(seconds)}s.jpg"
            cmd = [
                self.get_ffmpeg_exe(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-headers",
                headers,
                "-ss",
                f"{seconds:.1f}",
                "-i",
                video_stream_url,
                "-frames:v",
                "1",
                "-vf",
                f"scale=-2:{height}",
                "-q:v",
                "3",
                "-y",
                str(out_file),
            ]
            try:
                subprocess.run(cmd, check=True, capture_output=True, timeout=30)
                if out_file.exists() and out_file.stat().st_size > 0:
                    extracted.append((out_file, seconds))
            except Exception as exc:
                logger.warning(f"截帧失败 timestamp={seconds}: {exc}")
                continue

        return extracted
