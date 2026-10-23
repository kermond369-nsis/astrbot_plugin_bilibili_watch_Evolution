"""核心数据契约（Data Contracts）。

定义从原始抓取数据到清洗加工、门控路由以及最终呈现的全生命周期强类型模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class ProcessingMode(str, Enum):
    """门控决策的处理模式"""

    TEXT_OUTLINE = "text_outline"  # 教程/攻略向：字幕与目录主导，纯文本极速总结
    VISUAL_MEME = "visual_meme"    # 二次元/梗向：画面与弹幕情绪主导，视觉子代理深度观察
    HYBRID = "hybrid"              # 综合/长视频向：章节分块抽样


# =====================================================================
# 1. 原始采集层数据模型 (Raw Data)
# =====================================================================


@dataclass
class RawVideoMeta:
    """原始视频元信息"""

    bvid: str
    aid: int
    cid: int
    title: str
    desc: str
    up_name: str
    up_uid: int
    duration: int  # 秒
    pubdate: int   # 时间戳
    page: int = 1
    page_count: int = 1
    # 统计数据
    views: int = 0
    danmaku: int = 0
    replies: int = 0
    likes: int = 0
    coins: int = 0
    favorites: int = 0
    shares: int = 0


@dataclass
class RawComment:
    """原始评论数据"""

    uname: str
    content: str
    like: int
    pinned: bool = False
    replies: list[str] = field(default_factory=list)


@dataclass
class RawSubtitleItem:
    """原始单条字幕"""

    start: float  # 秒
    end: float    # 秒
    content: str


@dataclass
class RawDanmakuItem:
    """原始单条弹幕"""

    time_point: float  # 出现时间点（秒）
    text: str          # 弹幕内容


# =====================================================================
# 2. 清洗与特征提炼层数据模型 (Cleaned & Feature Extracted)
# =====================================================================


@dataclass
class CleanedOutlineItem:
    """从简介或评论中解析出的时间戳目录点（如 01:23 装备推荐）"""

    seconds: float
    title: str


@dataclass
class CleanedSubtitleParagraph:
    """清洗合并后的连贯字幕段落"""

    start_seconds: float
    end_seconds: float
    text: str
    density_score: float = 1.0  # 信息密度评分（高频词、转折词、疑问句等）


@dataclass
class HighEnergyPoint:
    """弹幕高能波峰点"""

    time_point: float           # 高能中心时间（秒）
    intensity: float            # 爆发热度指数 (相对基线)
    top_keywords: list[str]     # 该时间段核心弹幕梗词/情绪词 (如 ["前方高能", "笑死", "大刀"])


@dataclass
class CleanedVideoBundle:
    """标准化清洗后交付给门控与分析引擎的纯净数据集"""

    meta: RawVideoMeta
    clean_desc: str                         # 剥离广告/引流后的核心简介
    timestamps_outline: list[CleanedOutlineItem] # 提取到的视频目录结构
    subtitles: list[CleanedSubtitleParagraph]    # 清洗合并后的字幕段落
    pinned_comment: RawComment | None       # UP主置顶评论（含补充说明/配装表）
    curated_comments: list[str]             # 精选代表性观众观点（去广、去复读）
    high_energy_points: list[HighEnergyPoint]# 提纯后的弹幕高能点与梗词云
    
    # 辅助统计指标（供门控路由使用）
    subtitle_coverage_ratio: float = 0.0    # 字幕覆盖视频时长的比例 (0.0~1.0)
    has_outline: bool = False               # 是否成功提取到了结构化时间戳目录
    total_subtitle_words: int = 0           # 字幕总有效字数


# =====================================================================
# 3. 门控与执行决策模型 (Gating & Plan)
# =====================================================================


@dataclass
class GatingPlan:
    """门控中心生成的具体执行策略"""

    mode: ProcessingMode
    reason: str
    need_frames: bool = True
    target_frame_count: int = 0
    target_frame_timestamps: list[float] = field(default_factory=list)
    system_prompt_type: str = "default"
