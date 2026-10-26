"""小米 MiMo 语音识别引擎（MiMo-V2.5-ASR）。

文档与协议：基于 Chat Completions 协议封装，纯标准库 urllib 实现，无第三方库依赖。
特点：国内极速网络、普通话与各种中国方言、中英混杂识别能力极强，自带高质量标点。
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

from astrbot.api import logger
from core.asr.base import ASRResult, BaseASREngine


class MimoASREngine(BaseASREngine):
    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.xiaomimimo.com/v1",
        model: str = "mimo-v2.5-asr",
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
            raise ValueError("小米 MiMo API Key 未配置")

        fmt = audio_format.lower().strip(".")
        if fmt not in ("mp3", "wav"):
            fmt = "mp3"

        # MIME 类型对齐
        mime_type = "audio/wav" if fmt == "wav" else "audio/mp3"

        # Base64 编码
        encoded_audio = base64.b64encode(audio_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded_audio}"

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": data_url},
                        }
                    ],
                }
            ],
            "asr_options": {
                "language": language if language in ("zh", "en") else "auto",
            },
        }

        url = f"{self.base_url}/chat/completions"
        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "api-key": self.api_key,
            "Authorization": f"Bearer {self.api_key}",
        }

        req = urllib.request.Request(url, data=req_data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                data = json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            logger.error(f"MiMo ASR HTTP 失败 code={exc.code}: {body}")
            raise RuntimeError(f"MiMo ASR 接口错误 code={exc.code}: {body}") from exc
        except Exception as exc:
            logger.error(f"MiMo ASR 网络请求异常: {exc}")
            raise RuntimeError(f"MiMo ASR 请求异常: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            return ASRResult(text="", raw_response=data)

        content = choices[0].get("message", {}).get("content", "") or ""
        usage = data.get("usage") or {}
        duration = float(usage.get("seconds") or 0.0)

        return ASRResult(
            text=content.strip(),
            duration_seconds=duration,
            language=language,
            raw_response=data,
        )
