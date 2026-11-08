"""时间点解析（秒数 / mm:ss / hh:mm:ss）。"""

from __future__ import annotations

import re

_TS_RE = re.compile(r"^\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\s*$")
_SEC_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?\s*$", re.IGNORECASE)


def parse_time_point(value: str | int | float | None) -> float | None:
    """把用户输入解析成秒；无法解析返回 None。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        return seconds if seconds >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    match = _TS_RE.match(text)
    if match:
        hours = int(match.group(1) or 0)
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        frac = float("0." + match.group(4)) if match.group(4) else 0.0
        return hours * 3600 + minutes * 60 + seconds + frac
    match = _SEC_RE.match(text)
    if match:
        return float(match.group(1))
    return None


def format_time_point(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"
