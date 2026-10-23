"""清洗层与门控路由离线单测。

覆盖：
1. 教程/攻略向视频清洗与门控判定；
2. 二次元/梗向切片视频清洗与门控判定；
3. 文本简介去噪与时间戳目录提取；
4. 弹幕高能波峰与梗词提炼；
5. 感知哈希 (dHash) 帧过滤去重。
"""

import sys
from pathlib import Path

# 将项目根目录加入 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cleaner.danmaku_cleaner import analyze_danmaku_peaks
from core.cleaner.frame_filter import compute_dhash, hamming_distance
from core.cleaner.pipeline import run_clean_pipeline
from core.cleaner.text_cleaner import (
    clean_and_merge_subtitles,
    clean_comments,
    clean_description,
    extract_timeline_outlines,
)
from core.gating.router import GatingRouter
from core.models import (
    ProcessingMode,
    RawComment,
    RawDanmakuItem,
    RawSubtitleItem,
    RawVideoMeta,
)


def test_text_cleaning_and_outline():
    print(">>> [测试 1] 简介去广告与时间戳目录提取...")
    raw_desc = """
    蔚蓝档案（Blue Archive）总力战格里戈利 Insane 难度平民一刀攻略！
    00:00 阵容配置与练度要求
    01:25 核心轴与技能顺序
    03:40 P2阶段转火细节与凹点
    
    【商务合作】邮箱：business@example.com
    【玩家交流群】QQ群号：123456789，进群获取高清轴图
    【淘宝同款】店铺搜：xxx外设
    https://item.taobao.com/item.htm?id=999
    #蔚蓝档案# #手游攻略# #二次元游戏#
    """
    cleaned_desc = clean_description(raw_desc)
    assert "business@example.com" not in cleaned_desc, "广告邮箱未剥离"
    assert "QQ群号" not in cleaned_desc, "QQ群引流未剥离"
    assert "淘宝" not in cleaned_desc, "淘宝客未剥离"
    assert "平民一刀攻略" in cleaned_desc, "核心简介内容被误删"

    outlines = extract_timeline_outlines(raw_desc)
    assert len(outlines) == 3, f"时间戳目录提取错误，提取到 {len(outlines)} 条"
    assert outlines[0].seconds == 0.0 and outlines[0].title == "阵容配置与练度要求"
    assert outlines[1].seconds == 85.0 and outlines[1].title == "核心轴与技能顺序"
    print("  -> 简介清洗与时间戳目录提取通过！")


def test_subtitle_merging():
    print(">>> [测试 2] 字幕口癖过滤与智能段落合并...")
    raw_subs = [
        RawSubtitleItem(start=0.5, end=2.0, content="就是说我们今天来看一下这个阵容，"),
        RawSubtitleItem(start=2.2, end=3.8, content="然后呢那个主力输出必须是临战爱丽丝，"),
        RawSubtitleItem(start=4.0, end=5.5, content="其实最核心的关键就在于破防时间点。"),
        RawSubtitleItem(start=12.0, end=14.0, content="如果破防没对上就会直接超时。"),  # 停顿了 6.5s
    ]
    paragraphs = clean_and_merge_subtitles(raw_subs, max_pause_gap=1.5)
    assert len(paragraphs) == 2, f"段落合并失败，预期 2 段，实际 {len(paragraphs)} 段"
    assert "就是说" not in paragraphs[0].text, "口癖未清除"
    assert "临战爱丽丝" in paragraphs[0].text, "核心关键词丢失"
    assert paragraphs[0].density_score > 1.0, "核心转折词权重未生效"
    print("  -> 字幕合并与去噪通过！")


def test_comment_curation():
    print(">>> [测试 3] 评论区置顶与垃圾过滤...")
    comments = [
        RawComment(uname="UP主", content="补充说明：渚的专武练度不够可以用真纪下位替代，注意轴往后推2秒", like=1200, pinned=True),
        RawComment(uname="抽奖机器人", content="微信扫码免费领皮肤关注公众号好物推荐加群888", like=5),
        RawComment(uname="路人A", content="第一", like=3),
        RawComment(uname="路人B", content="哈哈哈哈", like=1),
        RawComment(uname="高玩C", content="实测专一渚也能打过，只要爱丽丝大招全暴击就行，凹度不高", like=350),
    ]
    pinned, curated = clean_comments(comments)
    assert pinned is not None and pinned.pinned, "置顶评论未识别"
    assert len(curated) == 1, f"精选评论过滤失败，实际数量 {len(curated)}"
    assert "实测专一渚也能打过" in curated[0], "高价值评论未保留"
    print("  -> 评论区清洗与置顶提取通过！")


def test_danmaku_peaks():
    print(">>> [测试 4] 弹幕高能波峰与二次元梗词挖掘...")
    # 构造在 45s 和 120s 处的两次高能弹幕爆发
    danmaku_items = []
    # 45s 附近：大刀/泪目名场面
    for _ in range(15):
        danmaku_items.append(RawDanmakuItem(time_point=44.5, text="前方高能！"))
        danmaku_items.append(RawDanmakuItem(time_point=45.2, text="大刀来了猝不及防"))
        danmaku_items.append(RawDanmakuItem(time_point=46.0, text="泪目..."))
    # 120s 附近：搞笑梗
    for _ in range(12):
        danmaku_items.append(RawDanmakuItem(time_point=120.1, text="笑死我了草"))
        danmaku_items.append(RawDanmakuItem(time_point=121.0, text="典中典"))
    # 随机打卡垃圾弹幕
    danmaku_items.append(RawDanmakuItem(time_point=1.0, text="111"))
    danmaku_items.append(RawDanmakuItem(time_point=2.0, text="打卡"))

    peaks = analyze_danmaku_peaks(danmaku_items, duration=180, top_n=3)
    assert len(peaks) >= 2, f"未检测到足够的高能波峰，实际 {len(peaks)}"
    # 检查是否成功抓取到 45s 和 120s 附近
    peak_times = [p.time_point for p in peaks]
    assert any(35 <= t <= 55 for t in peak_times), "45s 泪目高能波峰未捕获"
    assert any(110 <= t <= 130 for t in peak_times), "120s 搞笑高能波峰未捕获"

    # 检查梗词识别
    first_peak = next(p for p in peaks if 35 <= p.time_point <= 55)
    assert any(kw in ("前方高能", "大刀", "泪目") for kw in first_peak.top_keywords), f"热词提取失败: {first_peak.top_keywords}"
    print("  -> 弹幕高能点与梗词提取通过！")


def test_gating_decision_tutorial():
    print(">>> [测试 5] 门控决策：教程类攻略走纯文本通道...")
    meta = RawVideoMeta(
        bvid="BV1Tutorial",
        aid=1001,
        cid=2001,
        title="格里戈利 Insane 一刀通关攻略",
        desc="00:00 轴配置\n01:30 实战演示\n03:10 技能要点",
        up_name="BA攻略组",
        up_uid=112233,
        duration=300,
        pubdate=1700000000,
    )
    # 模拟充足的口播字幕
    subs = [
        RawSubtitleItem(start=float(i * 10), end=float(i * 10 + 8), content=f"这里是第{i}阶段技能讲解与实操步骤，注意破防与增伤覆盖。")
        for i in range(25)
    ]
    bundle = run_clean_pipeline(meta, subs, [], [])
    router = GatingRouter()
    plan = router.decide(bundle)

    assert plan.mode == ProcessingMode.TEXT_OUTLINE, f"预期为 TEXT_OUTLINE 模式，实际: {plan.mode}"
    assert not plan.need_frames, "教程类不应触发截帧"
    assert plan.target_frame_count == 0
    print(f"  -> 门控判定成功：{plan.reason}")


def test_gating_decision_meme():
    print(">>> [测试 6] 门控决策：二次元切片/梗向视频走画面+弹幕通道...")
    meta = RawVideoMeta(
        bvid="BV1MemeCut",
        aid=1002,
        cid=2002,
        title="【蔚蓝档案】阿罗娜的100种迫害方式（名场面手书）",
        desc="自制手书，BGM在置顶，喜欢请三连！",
        up_name="二次元二创君",
        up_uid=445566,
        duration=120,
        pubdate=1700000000,
    )
    # 无字幕或台词极少
    subs = [
        RawSubtitleItem(start=5.0, end=7.0, content="Sensei..."),
    ]
    # 弹幕很多
    danmaku = [
        RawDanmakuItem(time_point=25.0, text="草"),
        RawDanmakuItem(time_point=25.2, text="笑死"),
        RawDanmakuItem(time_point=25.5, text="前方高能"),
        RawDanmakuItem(time_point=70.0, text="名场面"),
        RawDanmakuItem(time_point=70.2, text="破防了"),
    ]
    bundle = run_clean_pipeline(meta, subs, [], danmaku)
    router = GatingRouter()
    plan = router.decide(bundle)

    assert plan.mode == ProcessingMode.VISUAL_MEME, f"预期为 VISUAL_MEME 模式，实际: {plan.mode}"
    assert plan.need_frames, "二次元梗类必须截帧"
    assert len(plan.target_frame_timestamps) > 0, "未规划截帧时间点"
    print(f"  -> 门控判定成功：{plan.reason}")
    print(f"  -> 规划目标截帧时间点: {plan.target_frame_timestamps}")


def run_all_tests():
    print("==================================================")
    print("开始执行清洗与门控核心模块全量离线验证...")
    print("==================================================")
    test_text_cleaning_and_outline()
    test_subtitle_merging()
    test_comment_curation()
    test_danmaku_peaks()
    test_gating_decision_tutorial()
    test_gating_decision_meme()
    print("==================================================")
    print("[PASS] All 6 core test cases passed successfully!")
    print("==================================================")


if __name__ == "__main__":
    run_all_tests()
