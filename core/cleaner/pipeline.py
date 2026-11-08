"""清洗流水线编排器（Cleaning Pipeline Orchestrator）。

汇聚文本、弹幕、评论各个清洗器，将粗糙原始数据提纯为 CleanedVideoBundle，
并额外产出「证据可信度」与「评论区高信息原文」两份分析侧材料。
"""

from __future__ import annotations

from typing import Sequence

from core.cleaner.danmaku_cleaner import analyze_danmaku_peaks
from core.cleaner.text_cleaner import (
    clean_and_merge_subtitles,
    clean_comments,
    clean_description,
    extract_timeline_outlines,
)
from core.models import (
    CleanedVideoBundle,
    RawComment,
    RawDanmakuItem,
    RawSubtitleItem,
    RawVideoMeta,
    SubtitleTrack,
)
from core.perception.evidence import (
    assess_subtitle_trust,
    extract_entities,
    is_gameplay,
    mine_axis_comments,
)


def run_clean_pipeline(
    meta: RawVideoMeta,
    raw_subtitles: Sequence[RawSubtitleItem],
    raw_comments: Sequence[RawComment],
    raw_danmaku: Sequence[RawDanmakuItem],
    subtitle_track: SubtitleTrack | None = None,
    gameplay_mode: str = "auto",
) -> CleanedVideoBundle:
    # 0. 字幕来源与可信度（错配的 AI 字幕不能进入事实材料）
    track = subtitle_track
    if track is None and raw_subtitles:
        track = SubtitleTrack(items=list(raw_subtitles), source="official")
    trust = assess_subtitle_trust(track, meta, raw_comments)
    usable_subtitles = list(raw_subtitles) if trust.usable else []

    # 1. 简介清洗
    clean_desc = clean_description(meta.desc)

    # 2. 评论清洗（提纯置顶与高赞精选）
    pinned_comment, curated_comments = clean_comments(raw_comments, max_count=12)

    # 3. 时间戳目录（野生目录君）挖掘：在简介与置顶评论中寻找
    outline_candidates = extract_timeline_outlines(meta.desc)
    if pinned_comment:
        outline_candidates.extend(extract_timeline_outlines(pinned_comment.content))
    # 去重并排序
    seen_sec = set()
    timestamps_outline = []
    for item in outline_candidates:
        if item.seconds not in seen_sec:
            seen_sec.add(item.seconds)
            timestamps_outline.append(item)
    timestamps_outline.sort(key=lambda x: x.seconds)

    # 4. 字幕重组与清洗
    subtitles = clean_and_merge_subtitles(usable_subtitles)
    total_words = sum(len(p.text) for p in subtitles)
    coverage = 0.0
    if meta.duration > 0 and subtitles:
        total_covered_sec = sum(p.end_seconds - p.start_seconds for p in subtitles)
        coverage = min(1.0, round(total_covered_sec / meta.duration, 2))

    # 5. 弹幕高能波峰与情绪梗词挖掘
    high_energy_points = analyze_danmaku_peaks(raw_danmaku, meta.duration)

    # 6. 评论区文字轴 / 高信息原文
    entities = extract_entities(meta, raw_comments)
    axis_comments = mine_axis_comments(raw_comments, entities)

    return CleanedVideoBundle(
        meta=meta,
        clean_desc=clean_desc,
        timestamps_outline=timestamps_outline,
        subtitles=subtitles,
        pinned_comment=pinned_comment,
        curated_comments=curated_comments,
        high_energy_points=high_energy_points,
        subtitle_coverage_ratio=coverage,
        has_outline=len(timestamps_outline) >= 2,
        total_subtitle_words=total_words,
        subtitle_track=track,
        subtitle_trust=trust,
        axis_comments=axis_comments,
        gameplay=is_gameplay(meta, mode=gameplay_mode),
    )
