"""OpenAI 兼容规范的 ASR 引擎（兼容 SiliconFlow / Groq / Faster-Whisper / 官方 Whisper）。

标准接口：POST {base_url}/audio/transcriptions (multipart/form-data)
纯标准库 urllib 实现 multipart 上传，零第三方依赖。
支持多语言识别（中文、英语、日语、韩语等），完美补充二次元多语言切片场景。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from astrbot.api import logger
from core.asr.base import ASRResult, BaseASREngine


class OpenAICompatibleASREngine(BaseASREngine):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
        model: str = "FunAudioLLM/SenseVoiceSmall",
    ):
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")
        self.model = model

    def transcribe(
        self,
        audio_bytes: bytes,
        audio_format: str = "mp3",
        language: str = "auto",
        timeout: int = 30,
    ) -> ASRResult:
        if not self.api_key:
            raise ValueError("ASR API Key 未配置")

        fmt = audio_format.lower().strip(".")
        filename = f"audio.{fmt}"
        content_type = "audio/mpeg" if fmt == "mp3" else f"audio/{fmt}"

        boundary = f"----WebKitFormBoundary{uuid.uuid4().hex}"
        body_parts: list[bytes] = []

        # 字段: model
        body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{self.model}\r\n".encode())

        # 字段: language (可选)
        if language and language != "auto":
            body_parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n".encode())

        # 字段: file
        file_header = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode()
        body_parts.append(file_header)
        body_parts.append(audio_bytes)
        body_parts.append(b"\r\n")

        # 结束边界
        body_parts.append(f"--{boundary}--\r\n".encode())
        full_body = b"".join(body_parts)

        url = f"{self.base_url}/audio/transcriptions"
        headers = {
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(url, data=full_body, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", "replace")
            logger.error(f"OpenAI ASR HTTP 失败 code={exc.code}: {err_text}")
            raise RuntimeError(f"OpenAI ASR 失败 code={exc.code}: {err_text}") from exc
        except Exception as exc:
            logger.error(f"OpenAI ASR 网络异常: {exc}")
            raise RuntimeError(f"OpenAI ASR 请求异常: {exc}") from exc

        text = data.get("text", "") or ""
        duration = float(data.get("duration") or 0.0)

        return ASRResult(
            text=text.strip(),
            duration_seconds=duration,
            language=language,
            raw_response=data,
        )
