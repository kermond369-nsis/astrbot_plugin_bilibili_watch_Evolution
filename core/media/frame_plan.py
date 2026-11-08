"""帧预算与场景选点（纯计算，无 IO）。

设计目标：
1. 用一条平滑曲线把「视频时长」换算成「需要看多少帧」，再按文本信息量增减；
2. 从等间隔采样点里挑出「切镜/高变化」时刻，保证叙事转折不被均匀采样漏掉；
3. 全程可预测、可单测，不依赖 ffmpeg / PIL。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Sequence

# 时长 -> 基础帧数（无文本、普通运动）。参考 Ref eyes_extreme.BASE_CURVE，略收紧。
BASE_CURVE: tuple[tuple[float, int], ...] = (
    (15, 6),
    (30, 8),
    (60, 12),
    (120, 16),
    (300, 20),
    (600, 24),
    (900, 28),
    (1800, 36),
    (3600, 44),
)

# 游戏实况需要更密的画面证据，避免整段战斗只抽到几帧
GAMEPLAY_FLOOR = 18


def _interp(points: Sequence[tuple[float, float]], value: float) -> float:
    value = max(0.0, float(value or 0))
    if value <= points[0][0]:
        return float(points[0][1])
    for (left_x, left_y), (right_x, right_y) in zip(points, points[1:]):
        if value <= right_x:
            ratio = (value - left_x) / max(1e-9, right_x - left_x)
            return left_y + (right_y - left_y) * ratio
    return float(points[-1][1])


def plan_frame_count(
    duration: float,
    text_chars: int = 0,
    has_outline: bool = False,
    gameplay: bool = False,
) -> int:
    """返回建议截帧数（6~48）。

    - 文本越少，画面越重要，帧数上调；
    - 有野生时间戳目录时，目录本身提供了结构，帧数下调；
    - 游戏实况给一个下限，保证操作栏/阶段变化至少被覆盖到。
    """
    base = _interp(BASE_CURVE, duration)
    if text_chars >= 1200:
        base *= 0.72
    elif text_chars >= 400:
        base *= 0.86
    elif text_chars > 0:
        base *= 0.95
    else:
        base *= 1.15
    if has_outline:
        base *= 0.85
    if gameplay:
        base = max(base, GAMEPLAY_FLOOR)
    return int(max(6, min(48, round(base))))


@dataclass
class SceneSelection:
    """选点结果：时间点 + 每个点被选中的理由。"""

    times: list[float] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def add(self, time_point: float, reason: str) -> None:
        self.times.append(float(time_point))
        self.reasons.append(reason)


def select_scene_times(
    sample_times: Sequence[float],
    change_scores: Sequence[float],
    target: int,
    duration: float,
    min_gap_ratio: float = 0.45,
) -> SceneSelection:
    """从等间隔样本中选 target 个时间点。

    采用「等宽时间箱 + 箱内最优」策略：
    1. 把安全区间均分成 target 个箱子，保证全片覆盖（不会只取打斗段落）；
    2. 每个箱子内优先选画面变化最大的样本，并用「离箱中心距离」做惩罚，
       避免连续两帧都贴在箱边界上；
    3. 掐掉片头片尾 3%（黑屏、logo、平台片尾）。
    """
    times = [float(t) for t in sample_times]
    if not times:
        return SceneSelection()

    duration = max(1.0, float(duration or times[-1] + 1))
    target = max(1, int(target))
    start_safe = duration * 0.03
    end_safe = duration * 0.97
    scores = [float(s or 0.0) for s in change_scores]
    if len(scores) < len(times):
        scores = scores + [0.0] * (len(times) - len(scores))

    span = max(1e-6, end_safe - start_safe)
    step = span / target
    selection = SceneSelection()

    for k in range(target):
        lo = start_safe + k * step
        hi = lo + step
        center = (lo + hi) / 2
        candidates = [i for i, t in enumerate(times) if lo <= t < hi]
        if not candidates:
            candidates = [min(range(len(times)), key=lambda i: abs(times[i] - center))]
        # 变化分越大越好，离箱中心越远扣分越多
        best = max(
            candidates,
            key=lambda i: scores[i] - abs(times[i] - center) / max(1e-6, step) * 4.0,
        )
        ts = round(times[best], 1)
        # 极端情况下同一秒被相邻箱重复选中，顺延到最近的未用样本
        if ts in selection.times:
            remaining = [i for i in candidates if round(times[i], 1) not in selection.times]
            if remaining:
                best = max(remaining, key=lambda i: scores[i])
                ts = round(times[best], 1)
        if ts in selection.times:
            continue
        reason = "画面变化" if scores[best] > 0 else "等宽覆盖"
        selection.add(ts, reason)

    order = sorted(range(len(selection.times)), key=lambda i: selection.times[i])
    return SceneSelection(
        times=[selection.times[i] for i in order],
        reasons=[selection.reasons[i] for i in order],
    )


def plan_sheet_grid(count: int, cols: int = 3, rows: int = 2) -> list[list[int]]:
    """把 N 帧按 3x2 一组切分，返回每张联表包含的帧序号（0-based）。"""
    per_sheet = max(1, cols * rows)
    return [list(range(i, min(i + per_sheet, count))) for i in range(0, count, per_sheet)]


def hud_interval(duration: float, max_sheets: int = 6, cells_per_sheet: int = 32) -> float:
    """让操作栏时间带在 max_sheets 张内覆盖全片所需的最小采样间隔（秒）。"""
    duration = max(1.0, float(duration or 0))
    capacity = max(1, int(max_sheets) * int(cells_per_sheet))
    return max(1.0, round(duration / capacity, 2))


def hud_sheet_count(duration: float, interval: float, cells_per_sheet: int = 32) -> int:
    if duration <= 0 or interval <= 0:
        return 1
    return max(1, math.ceil(duration / (interval * max(1, cells_per_sheet))))
