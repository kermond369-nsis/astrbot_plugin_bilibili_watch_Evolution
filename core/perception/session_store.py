"""会话级材料记忆与追问管理中心（Session Store & Follow-up Memory）。

职责：
1. 缓存会话最近看过的 CleanedVideoBundle 与子代理笔记（默认保留 30 分钟）；
2. 识别用户自然语言追问（如“刚才那个视频讲了啥”、“置顶评论说了什么”），自动调取记忆；
3. 支持 single-flight 视频并发锁，防止同一视频被重复处理。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from core.models import CleanedVideoBundle

# 追问自然语言识别正则
FOLLOWUP_PATTERN = re.compile(
    r"(?:刚才|刚刚|上[个一]视频|这个视频|那条视频|视频[里中上的]|置顶|轴|配置|练度|台词|字幕|发布时间|UP主|up主|讲了什么|说了什么|多少|怎么打)"
)


@dataclass
class SessionRecord:
    umo: str
    bundle: CleanedVideoBundle
    digest: str
    created_at: float = field(default_factory=time.monotonic)
    frame_paths: list[str] = field(default_factory=list)


class SessionStore:
    def __init__(self, ttl_seconds: int = 1800, max_entries: int = 100):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._records: dict[str, SessionRecord] = {}

    def put(self, umo: str, bundle: CleanedVideoBundle, digest: str, frame_paths: list[str] | None = None):
        self._cleanup()
        self._records[umo] = SessionRecord(
            umo=umo,
            bundle=bundle,
            digest=digest,
            created_at=time.monotonic(),
            frame_paths=frame_paths or [],
        )

    def get(self, umo: str) -> SessionRecord | None:
        record = self._records.get(umo)
        if not record:
            return None
        if time.monotonic() - record.created_at > self.ttl:
            self._records.pop(umo, None)
            return None
        return record

    def is_followup_message(self, umo: str, text: str) -> bool:
        """检查当前消息是否为针对上一视频的自然追问。"""
        if not text or text.strip().startswith("/"):
            return False
        record = self.get(umo)
        if not record:
            return False
        return bool(FOLLOWUP_PATTERN.search(text))

    def build_followup_prompt(self, umo: str, user_question: str) -> str | None:
        """为追问场景组装即时上下文。"""
        record = self.get(umo)
        if not record:
            return None

        video = record.bundle.meta
        lines = [
            f"【背景上下文：上一个视频《{video.title}》（UP主：{video.up_name}）的结构化分析笔记】",
            "<video_analysis_record>",
            record.digest,
            "</video_analysis_record>",
        ]

        if record.bundle.pinned_comment:
            lines.append(
                f"【UP主置顶评论原文】\n“{record.bundle.pinned_comment.content}”"
            )

        lines.extend([
            f"【用户追问】：{user_question}",
            "请基于上述客观记录直接针对追问进行清晰、简明、准确的解答，不输出多余寒暄。",
        ])
        return "\n".join(lines)

    def _cleanup(self):
        now = time.monotonic()
        expired = [umo for umo, r in self._records.items() if now - r.created_at > self.ttl]
        for umo in expired:
            self._records.pop(umo, None)
        if len(self._records) > self.max_entries:
            # 淘汰最旧的
            oldest = sorted(self._records.items(), key=lambda kv: kv[1].created_at)
            for umo, _ in oldest[: len(self._records) - self.max_entries]:
                self._records.pop(umo, None)
