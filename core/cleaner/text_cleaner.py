"""文本维度清洗引擎（Text Cleaner）。

负责视频简介去噪、字幕智能重组断句、评论垃圾过滤与时间戳目录（野生目录君）挖掘。
所有方法均为纯函数，无网络/无外部 IO 依赖。
"""

from __future__ import annotations

import re
from typing import Sequence

from core.models import (
    CleanedOutlineItem,
    CleanedSubtitleParagraph,
    RawComment,
    RawSubtitleItem,
)

# 常见引流/广告特征正则
AD_KEYWORDS = (
    "微信", "公众号", "淘宝", "店铺", "商务合作", "邮箱", "企鹅群", "qq群",
    "交流群", "加群", "电报", "telegram", "赞助", "patreon", "爱发电",
    "b站群", "打赏", "代练", "接单", "壁纸在群", "免责声明", "侵权请联系",
    "关注我", "私信", "互关", "好物推荐", "返利", "优惠券", "链接放在",
)
AD_LINE_PATTERN = re.compile(
    r"(?:v|vx|wx|qq|群号|合作|邮箱|淘宝|店铺|优惠券|进群|同款)[：:\s]*[a-zA-Z0-9_\-\.\@]{4,}",
    re.IGNORECASE,
)
URL_PATTERN = re.compile(r"https?://[^\s，。！？、）)】\]]+", re.IGNORECASE)

# 时间戳目录正则（匹配形如：01:23 装备推荐、1:05:20 终局打法）
OUTLINE_PATTERN = re.compile(
    r"(?:^|[^\d:])((?:(?:\d{1,2}:)?\d{1,2}:\d{2}))\s*([^\n\r,，。！？]{2,30})",
    re.MULTILINE,
)

# 常见口语废话/填充词正则（字幕清洗）
FILLER_WORDS_PATTERN = re.compile(
    r"(?:就是说|然后呢?|那什么|那个那个|嗯嗯?|啊啊?|呃呃?|也就是说|其实呢?)",
    re.IGNORECASE,
)


def parse_timestamp_to_seconds(ts_str: str) -> float | None:
    """将 mm:ss 或 hh:mm:ss 转换为秒数"""
    parts = ts_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except Exception:
        return None
    return None


def clean_description(desc: str, max_lines: int = 8) -> str:
    """清洗视频简介：

    1. 剥离网址、群号、商务合作、免责声明等推广垃圾；
    2. 过滤纯话题 Tag 堆砌；
    3. 截取前排真正说明视频内容的有效文本。
    """
    if not desc:
        return ""

    lines = [line.strip() for line in desc.splitlines()]
    clean_lines: list[str] = []

    for line in lines:
        if not line:
            continue

        # 检查是否命中典型的广告行
        line_lower = line.lower()
        if any(keyword in line_lower for keyword in AD_KEYWORDS):
            continue
        if AD_LINE_PATTERN.search(line):
            continue

        # 剥离 URL
        line_no_url = URL_PATTERN.sub("", line).strip()
        if not line_no_url:
            continue

        # 过滤全是 #话题 的行
        if line_no_url.startswith("#") and line_no_url.count("#") >= 3:
            continue

        clean_lines.append(line_no_url)
        if len(clean_lines) >= max_lines:
            break

    return "\n".join(clean_lines).strip()


def extract_timeline_outlines(text: str) -> list[CleanedOutlineItem]:
    """从简介或置顶/高赞评论中提取时间戳目录（野生目录君）。"""
    if not text:
        return []

    items: list[CleanedOutlineItem] = []
    seen_seconds: set[float] = set()

    for match in OUTLINE_PATTERN.finditer(text):
        ts_str = match.group(1).strip()
        title = match.group(2).strip(" -:：|丨")
        seconds = parse_timestamp_to_seconds(ts_str)

        if seconds is not None and seconds not in seen_seconds and len(title) >= 2:
            seen_seconds.add(seconds)
            items.append(CleanedOutlineItem(seconds=seconds, title=title))

    # 按时间升序排序
    items.sort(key=lambda x: x.seconds)
    return items


def clean_and_merge_subtitles(
    raw_subtitles: Sequence[RawSubtitleItem],
    max_pause_gap: float = 1.0,
    max_paragraph_duration: float = 25.0,
    max_paragraph_chars: int = 120,
) -> list[CleanedSubtitleParagraph]:
    """字幕智能清洗与段落合并：

    1. 清除口头禅、填充词、前后多余空格；
    2. 将短促的单行字幕按自然停顿（时间差 > max_pause_gap）与长度重组成连贯段落；
    3. 计算段落信息密度打分（是否包含专有名词/疑问句/核心转折）。
    """
    if not raw_subtitles:
        return []

    paragraphs: list[CleanedSubtitleParagraph] = []

    curr_start = raw_subtitles[0].start
    curr_end = raw_subtitles[0].end
    curr_texts: list[str] = []

    def flush_paragraph():
        nonlocal curr_start, curr_end, curr_texts
        if not curr_texts:
            return
        combined_text = "".join(curr_texts).strip()
        # 清洗填充词
        clean_text = FILLER_WORDS_PATTERN.sub("", combined_text).strip()
        if len(clean_text) >= 2:
            # 简单评估信息密度（疑问词、专有名词标点权重）
            score = 1.0
            if any(ch in clean_text for ch in "？?！!"):
                score += 0.5
            if any(w in clean_text for w in ("但是", "其实", "关键", "核心", "原理", "方法", "原因")):
                score += 0.8
            paragraphs.append(
                CleanedSubtitleParagraph(
                    start_seconds=curr_start,
                    end_seconds=curr_end,
                    text=clean_text,
                    density_score=round(score, 2),
                )
            )
        curr_texts.clear()

    for item in raw_subtitles:
        raw_text = item.content.strip()
        if not raw_text:
            continue

        # 如果与上一句的间隔时间过大，或者当前段落积累过长，切分新段落
        gap = item.start - curr_end
        paragraph_duration = item.end - curr_start
        char_count = sum(len(t) for t in curr_texts)

        if gap > max_pause_gap or paragraph_duration > max_paragraph_duration or char_count > max_paragraph_chars:
            flush_paragraph()
            curr_start = item.start

        curr_texts.append(raw_text)
        curr_end = item.end

    flush_paragraph()
    return paragraphs


def clean_comments(
    raw_comments: Sequence[RawComment],
    max_count: int = 10,
    min_length: int = 4,
) -> tuple[RawComment | None, list[str]]:
    """评论区深度清洗：

    1. 分离并优先保留 UP 置顶评论；
    2. 剔除营销号、抽奖接龙、纯表情、无营养刷屏（如“第一”、“前排”、“打卡”）；
    3. 提取具有观点代表性的观众精选评论。
    """
    pinned_comment: RawComment | None = None
    curated_comments: list[str] = []
    seen_texts: set[str] = set()

    trivial_patterns = ("第一", "打卡", "来了", "前排", "沙发", "哈哈哈哈", "草", "占座", "先赞后看")

    for comment in raw_comments:
        text = comment.content.strip()
        if not text:
            continue

        # 提取置顶
        if comment.pinned and pinned_comment is None:
            pinned_comment = comment
            continue

        # 基础过滤
        if len(text) < min_length:
            continue
        if any(text == p for p in trivial_patterns):
            continue
        if any(kw in text.lower() for kw in AD_KEYWORDS):
            continue

        # 相似/重复去重
        text_signature = text[:30]
        if text_signature in seen_texts:
            continue
        seen_texts.add(text_signature)

        curated_comments.append(f"{comment.uname}（赞 {comment.like}）：{text}")
        if len(curated_comments) >= max_count:
            break

    return pinned_comment, curated_comments
