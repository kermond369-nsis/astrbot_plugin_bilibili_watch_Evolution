"""ASR 转录结果特化清洗引擎（ASR Cleaner）。

针对语音识别固有缺陷进行二次清洗：
1. 过滤背景音乐/静音段的死循环幻觉（如“请订阅”、“谢谢观看”、“优酷独播”）；
2. 过滤连续字符/词组异常复读；
3. 二次元与游戏术语纠偏字典（Glossary Normalization）；
4. 将连续 ASR 文本自动切分为带时间戳或自然断句的 RawSubtitleItem。
"""

from __future__ import annotations

import re

from core.models import RawSubtitleItem

# 常见 ASR 静音/BGM 幻觉词库
ASR_HALLUCINATIONS = (
    "请不吝点赞", "订阅我的频道", "谢谢观看", "优酷独家播出", "爱奇艺独家",
    "字幕由", "未经著作权人", "请点赞投币", "欢迎在评论区留言", "敬请关注",
    "thank you for watching", "subtitles by", "please subscribe",
)

# 二次元/常见二游术语纠偏字典 (常见 ASR 误识别替换)
ANIME_GLOSSARY_REPLACEMENTS = [
    (re.compile(r"神色[，,。\s]*先森", re.IGNORECASE), "Sensei"),
    (re.compile(r"(?:邻占|临占|遴战|邻战)", re.IGNORECASE), "临战"),
    (re.compile(r"总力赞", re.IGNORECASE), "总力战"),
    (re.compile(r"大招充能", re.IGNORECASE), "EX技能充能"),
    (re.compile(r"爱利丝", re.IGNORECASE), "爱丽丝"),
    (re.compile(r"阿罗那", re.IGNORECASE), "阿罗娜"),
    (re.compile(r"白子[·\s]*带扣", re.IGNORECASE), "白子（骑行）"),
]


def clean_asr_transcript(text: str) -> str:
    """清洗 ASR 原始文本：幻觉过滤、复读抑制、术语纠偏。"""
    if not text:
        return ""

    cleaned = text.strip()

    # 1. 过滤幻觉词
    for hal in ASR_HALLUCINATIONS:
        cleaned = cleaned.replace(hal, "")

    # 2. 抑制重复字符/词语死循环 (如 "啊啊啊啊啊啊..." 或 "对对对对对对...")
    cleaned = re.sub(r"(.)\1{5,}", r"\1\1", cleaned)
    cleaned = re.sub(r"(.{2,4})\1{4,}", r"\1\1", cleaned)

    # 3. 术语纠偏
    for pattern, replacement in ANIME_GLOSSARY_REPLACEMENTS:
        cleaned = pattern.sub(replacement, cleaned)

    return cleaned.strip()


def convert_asr_text_to_subtitles(
    asr_text: str, total_duration: float
) -> list[RawSubtitleItem]:
    """将整段 ASR 文本（带标点）根据标点断句，并平摊近似时间戳为 RawSubtitleItem。"""
    clean_text = clean_asr_transcript(asr_text)
    if not clean_text:
        return []

    # 按标点断句
    sentences = [s.strip() for s in re.split(r"([。！？!？；;\n]+)", clean_text) if s.strip()]
    merged_sentences: list[str] = []
    # 重新把标点接在句子末尾
    i = 0
    while i < len(sentences):
        sentence = sentences[i]
        if i + 1 < len(sentences) and re.match(r"^[。！？!？；;\n]+$", sentences[i + 1]):
            sentence += sentences[i + 1]
            i += 2
        else:
            i += 1
        if len(sentence) >= 2:
            merged_sentences.append(sentence)

    if not merged_sentences:
        return [RawSubtitleItem(start=0.0, end=total_duration, content=clean_text)]

    # 按字符长度比例分配近似时间戳
    total_chars = sum(len(s) for s in merged_sentences)
    items: list[RawSubtitleItem] = []
    current_time = 0.0

    for s in merged_sentences:
        fraction = len(s) / max(1, total_chars)
        duration = max(1.0, fraction * total_duration)
        end_time = min(total_duration, current_time + duration)
        items.append(
            RawSubtitleItem(
                start=round(current_time, 2),
                end=round(end_time, 2),
                content=s,
            )
        )
        current_time = end_time

    return items
