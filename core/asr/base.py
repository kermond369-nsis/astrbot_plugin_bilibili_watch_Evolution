"""ASR 语音转文字统一抽象接口与数据结构。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ASRResult:
    text: str                       # 转录得到的纯文本
    duration_seconds: float = 0.0   # 音频时长
    language: str = "auto"          # 检测或使用的语种
    raw_response: dict | None = None


class BaseASREngine(ABC):
    """ASR 引擎抽象基类"""

    @abstractmethod
    def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "mp3",
        language: str = "auto",
        timeout: int = 30,
    ) -> ASRResult:
        """执行语音识别转录。

        :param audio_bytes: 原始音频二进制数据
        :param audio_format: 音频格式 (mp3 或 wav)
        :param language: 语种 ("auto", "zh", "en", "ja" 等)
        :param timeout: 超时时间（秒）
        """
        pass
