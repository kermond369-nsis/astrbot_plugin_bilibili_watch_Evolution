"""画面证据 / 字幕可信度 / 评论区文字轴 的离线单测（无网络、无 ffmpeg）。

覆盖：
1. 帧预算随时长与文本量变化；
2. 场景选点优先高变化点并保持间隔；
3. HUD 时间带的间隔能在限定张数内覆盖全片；
4. AI 字幕错配识别（健身文案 vs 蔚蓝档案视频）；
5. 评论区文字轴挖掘；
6. 游戏实况识别；
7. 联表合成与提示词里的证据索引。
"""

import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.media.frame_plan import (
    hud_interval,
    hud_sheet_count,
    plan_frame_count,
    select_scene_times,
)
from core.media.sampler import Sample, compose_contact_sheet, compose_hud_sheet
from core.models import (
    CleanedVideoBundle,
    CommentEvidence,
    GatingPlan,
    ProcessingMode,
    RawComment,
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
from core.perception.prompt_builder import (
    EvidenceBundle,
    GAMEPLAY_EXTRACTOR_SYSTEM_PROMPT,
    SheetInfo,
    build_analysis_input,
    pick_system_prompt,
)


def _ba_meta() -> RawVideoMeta:
    return RawVideoMeta(
        bvid="BV1MwxY63Eu9",
        aid=1,
        cid=2,
        title="【转载|蔚蓝档案】蓝甲赛特塔124层 水伊吹+心【制约解除作战】",
        desc="ケイが強ければチャリコ抜けると思う",
        up_name="G大调椭圆算子",
        up_uid=3,
        duration=374,
        pubdate=0,
        tags=["ブルアカ", "战术对抗赛", "教程攻略", "速通", "总力战", "碧蓝档案"],
    )


def _axis_comment() -> RawComment:
    text = (
        "伊吹选牌\n心（车白）\n4c auto完佳后 绿  心 芋 水白 车白\n"
        "2.9心奈 4.5kei 5佳（车白）\n减费黑子 芋吹心心奈\n5.8绿 9球kei佳车白\n"
        "心奈 心 水白 芋泥\n减费吹 3.11.1 车白\n7.7 黑子 kei佳（车白）绿\n"
        "7球芋 2.55.9车白 5心\n12心奈 减费 吹水白kei佳心\n"
        "ps:前面绿给心是为了提高心攻速加速心触发ns减攻保住车白 我没心ns的时候车白就会挂掉"
    )
    return RawComment(uname="帅气的李晓果", content=text, like=1)


def test_frame_budget():
    print(">>> [测试 1] 帧预算随时长/文本量变化...")
    short = plan_frame_count(60)
    long = plan_frame_count(600)
    assert 6 <= short <= long <= 48, (short, long)
    assert plan_frame_count(600, text_chars=3000) < long, "文本充足时应减少画面帧"
    assert plan_frame_count(120, gameplay=True) >= 18, "游戏实况应有帧数下限"
    print(f"  -> 60s={short} 帧, 600s={long} 帧，文本加权与游戏下限正常")


def test_scene_selection():
    print(">>> [测试 2] 场景选点优先高变化点并保持间隔...")
    times = [float(i) for i in range(0, 120, 2)]
    scores = [0.0] * len(times)
    scores[10] = 50.0  # 20s 处一个大切镜
    scores[40] = 40.0  # 80s 处第二个
    picked = select_scene_times(times, scores, target=12, duration=120)
    assert len(picked.times) == 12, f"选点数量错误: {len(picked.times)}"
    assert any(abs(t - 20) <= 2 for t in picked.times), f"漏掉 20s 切镜: {picked.times}"
    assert any(abs(t - 80) <= 2 for t in picked.times), f"漏掉 80s 切镜: {picked.times}"
    assert all(b - a >= 2.0 for a, b in zip(picked.times, picked.times[1:])), "选点过密"
    print(f"  -> 选点 {len(picked.times)} 个，切镜命中，间隔正常")


def test_hud_interval():
    print(">>> [测试 3] HUD 时间带覆盖全片...")
    for duration in (120, 374, 900):
        gap = hud_interval(duration, max_sheets=6, cells_per_sheet=32)
        sheets = hud_sheet_count(duration, gap, 32)
        assert sheets <= 6, f"{duration}s 需要 {sheets} 张，超过上限"
        assert gap * 32 * sheets >= duration - 0.1, f"{duration}s 覆盖不足"
    print("  -> 2/6/15 分钟均在 6 张内覆盖")


def test_subtitle_trust():
    print(">>> [测试 4] AI 字幕错配识别...")
    meta = _ba_meta()
    comments = [_axis_comment()]
    fitness = "".join(
        f"第{i}组训练要做五分化，把全身练一遍，注意肌肉恢复和组数安排。" for i in range(40)
    )
    track = SubtitleTrack(
        items=[RawSubtitleItem(start=i * 2.0, end=i * 2.0 + 2, content=fitness[i * 20 : i * 20 + 20]) for i in range(40)],
        source="ai",
        lang="ai-zh",
    )
    trust = assess_subtitle_trust(track, meta, comments)
    assert trust.level == "suspect", f"错配字幕未识别: {trust.level} / {trust.reasons}"

    good = SubtitleTrack(
        items=[RawSubtitleItem(start=0, end=5, content="蓝甲赛特塔124层，水伊吹和心的总力战作业，注意车白生存。")],
        source="ai",
        lang="ai-zh",
    )
    assert assess_subtitle_trust(good, meta, comments).level == "ok", "正常 AI 字幕被误判"

    official = SubtitleTrack(items=good.items, source="official", lang="zh-Hans")
    assert assess_subtitle_trust(official, meta, comments).level == "official"

    asr = SubtitleTrack(
        items=[RawSubtitleItem(start=0, end=5, content="这里是战斗音效和人声转录")],
        source="asr",
        lang="auto",
    )
    assert assess_subtitle_trust(asr, meta, comments).level == "ok", "ASR 转录被误判为错配"

    music_meta = RawVideoMeta(
        bvid="BV1", aid=1, cid=1, title="【原创MV】夏日", desc="", up_name="u", up_uid=1,
        duration=180, pubdate=0, tags=["音乐", "原创曲"],
    )
    assert assess_subtitle_trust(track, music_meta, []).level == "ok", "音乐区不应被拦截"
    print(f"  -> 错配=>{trust.level}，正常=>ok，官方=>official，音乐区=>ok")


def test_axis_comments():
    print(">>> [测试 5] 评论区文字轴挖掘...")
    comments = [
        _axis_comment(),
        RawComment(uname="路人", content="哈哈哈哈笑死", like=99),
        RawComment(uname="提问", content="4星心能打吗必须母s2吗", like=0),
    ]
    picked = mine_axis_comments(comments, extract_entities(_ba_meta(), comments))
    assert picked, "未挖到文字轴"
    assert "伊吹选牌" in picked[0].content
    assert picked[0].like == 1
    print(f"  -> 命中 {len(picked)} 条，首条: @{picked[0].uname} {picked[0].reason}")


def test_gameplay_detection():
    print(">>> [测试 6] 游戏实况识别...")
    assert is_gameplay(_ba_meta()), "蔚蓝档案攻略未识别为游戏"
    vlog = RawVideoMeta(
        bvid="BV2", aid=2, cid=2, title="周末去海边散步", desc="随手记录", up_name="u", up_uid=1,
        duration=300, pubdate=0, tags=["生活", "vlog"],
    )
    assert not is_gameplay(vlog), "生活区 vlog 被误判为游戏"
    print("  -> 游戏 True / vlog False")


def test_sheet_and_prompt():
    print(">>> [测试 7] 联表合成与提示词证据索引...")
    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        samples = []
        for i in range(3):
            path = tmp_path / f"f{i}.jpg"
            Image.new("RGB", (960, 540), (30 + i * 20, 60, 90)).save(path)
            samples.append(Sample(index=i + 1, time=i * 10.0, thumb=path))
        scene_sheet = compose_contact_sheet(samples, tmp_path / "scene.jpg")
        assert scene_sheet and scene_sheet.exists(), "场景联表未生成"
        with Image.open(scene_sheet) as img:
            assert img.size == (1920, 764), img.size

        hud_samples = []
        for i in range(4):
            path = tmp_path / f"h{i}.jpg"
            Image.new("RGB", (930, 200), (10, 20 + i * 10, 40)).save(path)
            hud_samples.append(Sample(index=i + 1, time=i * 2.0, hud=path))
        hud_sheet = compose_hud_sheet(hud_samples, tmp_path / "hud.jpg")
        assert hud_sheet and hud_sheet.exists(), "HUD 时间带未生成"
        with Image.open(hud_sheet) as img:
            assert img.size == (1600, 816), img.size

        meta = _ba_meta()
        bundle = CleanedVideoBundle(
            meta=meta,
            clean_desc="",
            timestamps_outline=[],
            subtitles=[],
            pinned_comment=None,
            curated_comments=["@观众（赞 3）：伤害有点缺"],
            high_energy_points=[],
            subtitle_track=SubtitleTrack(items=[], source="none"),
            subtitle_trust=None,
            axis_comments=[CommentEvidence(uname="帅气的李晓果", like=1, content="伊吹选牌 4c auto", reason="疑似文字轴")],
            gameplay=True,
        )
        plan = GatingPlan(
            mode=ProcessingMode.VISUAL_MEME,
            reason="gameplay",
            target_frame_count=3,
            system_prompt_type="gameplay",
            gameplay=True,
            need_hud=True,
        )
        evidence = EvidenceBundle(
            sheets=[
                SheetInfo(index=1, kind="scene", start=0, end=20, cells=3),
                SheetInfo(index=2, kind="hud", start=0, end=6, cells=4, interval=2.0),
            ],
            frame_times=[0, 10, 20],
            hud_interval=2.0,
            sample_note="单测",
        )
        prompt = build_analysis_input(bundle, plan, evidence=evidence)
        for needle in ("图像证据索引", "图 2", "底栏操作栏时间带", "评论区高信息原文", "伊吹选牌"):
            assert needle in prompt, f"提示词缺少 {needle}"
        assert "A 档" in pick_system_prompt(plan) or "画面可复核" in pick_system_prompt(plan)
        assert GAMEPLAY_EXTRACTOR_SYSTEM_PROMPT in pick_system_prompt(plan)
        print(f"  -> 联表尺寸正确，prompt {len(prompt)} 字，证据索引与文字轴均已注入")


def run_all_tests():
    print("=" * 52)
    print("开始执行画面证据 / 字幕可信度 / 文字轴离线验证...")
    print("=" * 52)
    test_frame_budget()
    test_scene_selection()
    test_hud_interval()
    test_subtitle_trust()
    test_axis_comments()
    test_gameplay_detection()
    test_sheet_and_prompt()
    print("=" * 52)
    print("[PASS] All perception tests passed successfully!")
    print("=" * 52)


if __name__ == "__main__":
    run_all_tests()
