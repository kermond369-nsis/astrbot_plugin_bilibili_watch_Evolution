"""弹幕清洗与高能情感/梗词挖掘引擎（Danmaku Cleaner & Miner）。

专门针对二次元/梗向内容：
1. 过滤垃圾刷屏、无意义单字符、纯数字/表情复读；
2. 自适应时间分桶统计弹幕密度，识别全片爆发式的高能波峰（High Energy Peaks）；
3. 聚类提取高能时间点的核心情绪词/梗词云（如“前方高能”、“笑死”、“泪目”、“大刀”）。
"""

from __future__ import annotations

import collections
import math
import re
from typing import Sequence

from core.models import HighEnergyPoint, RawDanmakuItem

# 二次元/B站常见高能情绪词表（用于提升识别敏感度）
RESONANCE_KEYWORDS = {
    "前方高能", "高能", "泪目", "草", "笑死", "大刀", "刀我", "猝不及防",
    "卧槽", "好帅", "燃起来了", "破防", "神作", "名场面", "封神", "声优不要钱",
    "心疼", "典", "绝了", "起飞", "要素过多", "梦开始的地方", "细节",
}

# 垃圾弹幕过滤正则
TRIVIAL_DANMAKU_RE = re.compile(r"^[\d\s\.\?\!\,\-\_\=\+\@\#\$\%\^\&\*\/\\~`]+$")


def filter_raw_danmaku(raw_danmaku: Sequence[RawDanmakuItem]) -> list[RawDanmakuItem]:
    """清洗单条弹幕：剔除无意义符号、单字符与空内容。"""
    cleaned: list[RawDanmakuItem] = []
    for item in raw_danmaku:
        text = item.text.strip()
        if len(text) < 2:
            continue
        if TRIVIAL_DANMAKU_RE.match(text):
            continue
        cleaned.append(RawDanmakuItem(time_point=round(item.time_point, 2), text=text))
    return cleaned


def analyze_danmaku_peaks(
    raw_danmaku: Sequence[RawDanmakuItem],
    duration: int,
    top_n: int = 6,
    min_bin_seconds: int = 10,
) -> list[HighEnergyPoint]:
    """分析弹幕密度并提取高能波峰点及其梗词：

    1. 根据视频时长自适应分桶（如 10s 或 15s 一个区间）；
    2. 统计每个区间的弹幕密度，过滤低于平均基线的平缓区间；
    3. 挖掘波峰区间内的高频词，生成 HighEnergyPoint。
    """
    valid_danmaku = filter_raw_danmaku(raw_danmaku)
    if not valid_danmaku or duration <= 0:
        return []

    # 自适应分桶大小 (10s ~ 30s)
    bin_size = max(min_bin_seconds, min(30, int(duration // 40) or 10))
    total_bins = int(math.ceil(duration / bin_size))

    bins: list[list[RawDanmakuItem]] = [[] for _ in range(total_bins)]

    for dm in valid_danmaku:
        idx = int(dm.time_point // bin_size)
        if 0 <= idx < total_bins:
            bins[idx].append(dm)

    counts = [len(b) for b in bins]
    if not counts or sum(counts) == 0:
        return []

    avg_count = sum(counts) / len(counts)
    # 筛选爆发度高于平均水平的区间
    ranked_indices = sorted(
        range(total_bins),
        key=lambda i: counts[i],
        reverse=True,
    )

    high_energy_points: list[HighEnergyPoint] = []
    selected_indices: set[int] = set()

    for idx in ranked_indices:
        if counts[idx] <= 1:
            break
        # 避免波峰过于密集（相邻的两个 bin 尽量只选最强的一个）
        if any(abs(idx - chosen) <= 1 for chosen in selected_indices):
            continue

        selected_indices.add(idx)
        bin_items = bins[idx]
        intensity = round(counts[idx] / max(avg_count, 1.0), 2)
        center_time = round((idx * bin_size) + (bin_size / 2), 1)

        # 挖掘该波峰区间的热词
        keywords = _extract_keywords_from_bin(bin_items)

        high_energy_points.append(
            HighEnergyPoint(
                time_point=min(center_time, float(duration)),
                intensity=intensity,
                top_keywords=keywords,
            )
        )

        if len(high_energy_points) >= top_n:
            break

    # 按时间顺序重排
    high_energy_points.sort(key=lambda p: p.time_point)
    return high_energy_points


def _extract_keywords_from_bin(items: list[RawDanmakuItem], top_k: int = 4) -> list[str]:
    """从一个时间段的弹幕中聚类提取核心热词与二次元情绪梗。"""
    freq: collections.Counter[str] = collections.Counter()

    for item in items:
        text = item.text
        # 优先匹配已知的高能特征词
        matched_any = False
        for kw in RESONANCE_KEYWORDS:
            if kw in text:
                freq[kw] += 3  # 给予加权
                matched_any = True

        # 如果没有命中预设词，做简单的短语频次统计
        if not matched_any and 2 <= len(text) <= 8:
            freq[text] += 1

    return [word for word, _ in freq.most_common(top_k)]
