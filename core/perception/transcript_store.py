"""字幕/ASR 原文磁盘缓存与检索（供 bilibili_transcript 二次查询直读）。

只存文字。内存里的 SessionStore 过期或进程重启后，仍能回答「第 3 分钟说了什么」。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TranscriptLine:
    start: float
    end: float
    text: str


@dataclass
class TranscriptRecord:
    bvid: str
    title: str = ""
    source: str = "none"  # official | ai | asr | none
    trust: str = "none"   # official | ok | weak | suspect | none
    duration: float = 0.0
    lines: list[TranscriptLine] = field(default_factory=list)
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "bvid": self.bvid,
            "title": self.title,
            "source": self.source,
            "trust": self.trust,
            "duration": self.duration,
            "updated_at": self.updated_at,
            "lines": [[line.start, line.end, line.text] for line in self.lines],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TranscriptRecord":
        lines = [
            TranscriptLine(start=float(row[0]), end=float(row[1]), text=str(row[2]))
            for row in data.get("lines") or []
            if isinstance(row, (list, tuple)) and len(row) >= 3
        ]
        return cls(
            bvid=str(data.get("bvid") or ""),
            title=str(data.get("title") or ""),
            source=str(data.get("source") or "none"),
            trust=str(data.get("trust") or "none"),
            duration=float(data.get("duration") or 0),
            lines=lines,
            updated_at=float(data.get("updated_at") or 0),
        )


class TranscriptStore:
    def __init__(self, root: Path, ttl_hours: int = 72, max_entries: int = 200):
        self.root = Path(root)
        self.ttl_seconds = max(1, int(ttl_hours)) * 3600
        self.max_entries = max(1, int(max_entries))

    def _path(self, bvid: str) -> Path:
        safe = "".join(ch for ch in bvid if ch.isalnum() or ch in "-_") or "unknown"
        return self.root / f"{safe}.json"

    def save(self, record: TranscriptRecord) -> None:
        if not record.bvid or not record.lines:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        record.updated_at = time.time()
        path = self._path(record.bvid)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record.to_dict(), ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        self.cleanup()

    def get(self, bvid: str) -> TranscriptRecord | None:
        path = self._path(bvid)
        if not path.exists():
            return None
        try:
            record = TranscriptRecord.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            return None
        if self.ttl_seconds and time.time() - record.updated_at > self.ttl_seconds:
            path.unlink(missing_ok=True)
            return None
        return record

    def has(self, bvid: str) -> bool:
        return self.get(bvid) is not None

    def search(
        self,
        bvid: str,
        keyword: str = "",
        start: float = 0.0,
        end: float | None = None,
        limit: int = 30,
    ) -> list[TranscriptLine]:
        record = self.get(bvid)
        if not record:
            return []
        end = float(end) if end is not None else float("inf")
        keyword = (keyword or "").strip().lower()
        hits: list[TranscriptLine] = []
        for line in record.lines:
            if line.end < start or line.start > end:
                continue
            if keyword and keyword not in line.text.lower():
                continue
            hits.append(line)
            if len(hits) >= max(1, limit):
                break
        return hits

    def cleanup(self) -> None:
        if not self.root.exists():
            return
        now = time.time()
        files = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for index, path in enumerate(files):
            expired = self.ttl_seconds and now - path.stat().st_mtime > self.ttl_seconds
            if expired or index >= self.max_entries:
                path.unlink(missing_ok=True)
