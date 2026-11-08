"""证据可信度与评论区高信息挖掘（纯函数，无网络/无 IO）。

解决的三个具体问题：
1. B 站 AI 字幕可能与视频内容完全无关（错配），不能直接当作事实喂给模型；
2. 评论区里常有观众手写的「文字轴 / 配队 / 时间点」，比模型瞎猜可靠得多；
3. 游戏实况需要专门的识别信号，走更密的画面证据。
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Sequence

from core.models import (
    CommentEvidence,
    RawComment,
    RawVideoMeta,
    SubtitleTrack,
    SubtitleTrust,
)

CJK_RE = re.compile(r"[\u4e00-\u9fff]")
TIMECODE_RE = re.compile(r"(?:^|[^\d])((?:\d{1,2}:)?\d{1,2}:\d{2})(?!\d)")
NUM_RE = re.compile(r"\d+(?:\.\d+)?")

# 高频虚词：由这些字组成的 3-gram 不作为「专有名词锚点」
STOP_CHARS = set(
    "的了是在我你他她它们这那个一不有和就都而及与着或一个上中下也为以对到说会要可以没有我们你们他们"
    "什么怎么这个那个就是然后但是因为所以如果还是已经可能自己现在知道觉得时候一样不是很多比较一下出来"
    "起来过去回来里外前后中边儿么呢吧啊哦呀嘛哈很太更最又再才只也还"
)

# 常见但无区分度的 3-gram（避免「的时候」这类词把错配字幕误判成正常）
NOISE_GRAMS = {
    "的时候", "有没有", "为什么", "不知道", "一样的", "我觉得", "这个视", "个视频",
    "什么的", "真的是", "可以的", "就是说", "然后呢", "还是说", "这样的", "那样的",
    "一下子", "一会儿", "可能是", "应该是", "不会的", "对不对", "怎么办", "怎么回事",
    "看这个", "看一下", "这个问", "个问题", "大家的", "大家好", "今天给", "天给大",
}

# 游戏/攻略向关键词（标题、简介、标签、分区）
GAMEPLAY_KEYWORDS = (
    "攻略", "打法", "速通", "阵容", "配队", "练度", "轴", "总力战", "大决战", "制约解除",
    "深渊", "危机合约", "剿灭", "通关", "实况", "操作", "教程", "作业", "凹", "一刀", "两刀",
    "boss", "副本", "爬塔", "竞速", "无伤", "配装", "技能循环", "起手", "循环", "轴图",
    "手机游戏", "网络游戏", "单机游戏", "游戏录屏", "实战演示", "战术对抗",
)

# 音乐/舞蹈区不做字幕错配拦截（歌词与标题天然低重合）
MUSIC_TAGS = ("音乐", "MV", "翻唱", "原创曲", "演奏", "舞蹈", "lyrics", "vocaloid")

AXIS_KEYWORDS = (
    "轴", "文字轴", "选牌", "起手", "开局", "循环", "顺序", "费用", "减费", "auto", "自动",
    "阶段", "转阶段", "凹", "秒", "球", "车白", "一刀", "两刀", "作业", "轴图",
)

# 观众「让 AI 生成轴」的请求不是轴本身，不能当证据
REQUEST_PATTERNS = (
    "帮我", "帮忙", "生成一份", "生成一个", "写一份", "总结一下", "整理一下", "求个",
)
AI_ADDRESS = ("deepseek", "chatgpt", "gpt", "claude", "gemini", "ai", "豆包", "kimi")


def _cjk_grams(text: str, n: int) -> list[str]:
    chars = "".join(CJK_RE.findall(text or ""))
    return [chars[i : i + n] for i in range(max(0, len(chars) - n + 1))]


def _is_noise_gram(gram: str) -> bool:
    return gram in NOISE_GRAMS or all(ch in STOP_CHARS for ch in gram)


def extract_entities(
    meta: RawVideoMeta,
    comments: Sequence[RawComment] = (),
    max_items: int = 24,
) -> list[str]:
    """抽取视频的「专有名词锚点」：标题/标签里的 3-gram + 评论区重复出现的 3-gram。"""
    counter: Counter[str] = Counter()
    for text in [meta.title, *meta.tags]:
        for gram in _cjk_grams(text, 3):
            if not _is_noise_gram(gram):
                counter[gram] += 3
    for comment in comments:
        for gram in _cjk_grams(comment.content, 3):
            if not _is_noise_gram(gram):
                counter[gram] += 1
    # 只保留出现两次以上的（标题/标签天然得 3 分）
    entities = [gram for gram, score in counter.most_common(max_items * 2) if score >= 2]
    return entities[:max_items]


def assess_subtitle_trust(
    track: SubtitleTrack | None,
    meta: RawVideoMeta,
    comments: Sequence[RawComment] = (),
) -> SubtitleTrust:
    """判断字幕是否值得作为事实依据。

    - official：官方人工字幕，直接可信；
    - ok：与标题/标签/评论有专有名词交集；
    - weak：AI 字幕但没有强反证，可参考、需与画面互证；
    - suspect：AI 字幕与视频的专有名词零交集，且评论/标签足以描述视频主题，
      高度疑似错配——不进入分析材料。
    """
    if track is None or not track.items:
        return SubtitleTrust(level="none", reasons=["未获取到字幕"])

    text = "".join(item.content for item in track.items)
    if track.source == "official":
        return SubtitleTrust(level="official", reasons=["官方人工字幕"])

    tags_lower = " ".join(meta.tags).lower()
    if any(tag.lower() in tags_lower for tag in MUSIC_TAGS):
        return SubtitleTrust(level="ok", reasons=["音乐/舞蹈类视频，不做字幕主题拦截"])

    entities = extract_entities(meta, comments)
    hits = [entity for entity in entities if entity in text]
    chars = len(CJK_RE.findall(text))

    if track.source == "asr":
        # ASR 一定来自本视频音轨，但音轨本身可能与画面主题无关（如换音频规避版权）
        if hits:
            return SubtitleTrust(
                level="ok",
                reasons=[f"ASR 转录与标题/评论共享 {len(hits)} 个专有名词锚点"],
                entity_hits=hits,
            )
        if len(entities) >= 6 and chars >= 250:
            return SubtitleTrust(
                level="weak",
                reasons=["ASR 转录来自本视频音轨，但与标题/标签/评论无专有名词交集，音轨内容可能与画面无关"],
            )
        return SubtitleTrust(level="ok", reasons=["来自本视频音轨的 ASR 转录"])

    if hits:
        return SubtitleTrust(
            level="ok",
            reasons=[f"AI 字幕与标题/评论共享 {len(hits)} 个专有名词锚点"],
            entity_hits=hits,
        )
    if len(entities) >= 6 and chars >= 250:
        return SubtitleTrust(
            level="suspect",
            reasons=[
                f"AI 字幕 {chars} 字，与标题/标签/评论的 {len(entities)} 个专有名词零交集，疑似与画面无关",
            ],
        )
    return SubtitleTrust(
        level="weak",
        reasons=["AI 字幕，未找到可交叉验证的专有名词，仅作参考"],
    )


def mine_axis_comments(
    comments: Sequence[RawComment],
    existing_entities: Sequence[str] = (),
    max_items: int = 3,
    max_chars: int = 1200,
) -> list[CommentEvidence]:
    """从评论区挖「文字轴 / 配队 / 时间点」这类高信息原文。"""
    scored: list[tuple[float, CommentEvidence]] = []
    for comment in comments:
        text = (comment.content or "").strip()
        if len(text) < 30:
            continue
        lowered = text.lower()
        # 「帮我生成一份轴」这类请求不是轴本身
        if any(pattern in lowered for pattern in REQUEST_PATTERNS) and any(
            name in lowered for name in AI_ADDRESS
        ):
            continue
        axis_hits = [kw for kw in AXIS_KEYWORDS if kw in lowered]
        numbers = NUM_RE.findall(text)
        timecodes = TIMECODE_RE.findall(text)
        entity_hits = [entity for entity in existing_entities if entity in text]

        score = 0.0
        score += 2.0 * len(axis_hits)
        score += 0.8 * len(timecodes)
        score += min(4.0, len(numbers) / 6)
        score += 0.6 * len(entity_hits)
        score += min(2.0, len(text) / 200)
        if "轴" in lowered:
            score += 3.0
        if score < 3.5:
            continue

        reason_bits = []
        if "轴" in lowered or any(kw in lowered for kw in ("选牌", "起手", "循环", "顺序")):
            reason_bits.append("疑似文字轴")
        if timecodes or len(numbers) >= 6:
            reason_bits.append("时间点/数值密集")
        if entity_hits:
            reason_bits.append("含视频专有名词")
        scored.append(
            (
                score,
                CommentEvidence(
                    uname=comment.uname,
                    like=comment.like,
                    content=text[:max_chars],
                    reason="、".join(reason_bits) or "高信息评论",
                ),
            )
        )

    scored.sort(key=lambda item: (item[0], item[1].like), reverse=True)
    return [item[1] for item in scored[:max_items]]


def is_gameplay(meta: RawVideoMeta, extra_text: str = "", mode: str = "auto") -> bool:
    """游戏实况/攻略识别：标题权重最高，其次标签与分区。

    mode: auto 自动判定 / on 强制按游戏实况处理 / off 强制关闭。
    """
    mode = (mode or "auto").strip().lower()
    if mode == "on":
        return True
    if mode == "off":
        return False

    title = (meta.title or "").lower()
    haystack = " ".join([meta.title or "", meta.desc or "", " ".join(meta.tags), extra_text]).lower()
    score = 0
    for keyword in GAMEPLAY_KEYWORDS:
        if keyword.lower() in title:
            score += 2
        elif keyword.lower() in haystack:
            score += 1
    if "游戏" in (meta.tname or ""):
        score += 3
    if any(tag in ("游戏", "手游", "单机", "网游", "游戏录屏") for tag in meta.tags):
        score += 2
    return score >= 3


def summarize_comment_entities(comments: Iterable[RawComment], limit: int = 6) -> list[str]:
    counter: Counter[str] = Counter()
    for comment in comments:
        for gram in set(_cjk_grams(comment.content, 3)):
            if not _is_noise_gram(gram):
                counter[gram] += 1
    return [gram for gram, count in counter.most_common(limit) if count >= 2]
