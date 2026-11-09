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

STUB_ROOT = PROJECT_ROOT / "tests" / "stub"
if str(STUB_ROOT) not in sys.path:
    sys.path.insert(0, str(STUB_ROOT))

from core.media.frame_plan import (
    hud_interval,
    hud_sheet_count,
    plan_frame_count,
    select_scene_times,
)
from core.client.bili_client import extract_page_number, with_page
from core.gating.router import GatingRouter
from core.media.sampler import (
    Sample,
    _load_cache,
    build_filter_complex,
    choose_hud_window,
    compose_contact_sheet,
    compose_hud_sheet,
    crop_hud_frame,
    estimate_content_box,
    hud_crop_fraction,
)
from core.models import (
    CleanedSubtitleParagraph,
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
    fallback_image_indices,
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
            assert img.width == 1600 and 700 <= img.height <= 1200, img.size

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


def test_evidence_regressions():
    print(">>> [测试 8] HUD 配置/降级/竖屏/缓存/分P 回归...")
    from dataclasses import replace

    from PIL import Image

    meta = _ba_meta()
    bundle = CleanedVideoBundle(
        meta=meta,
        clean_desc="",
        timestamps_outline=[],
        subtitles=[],
        pinned_comment=None,
        curated_comments=[],
        high_energy_points=[],
        gameplay=True,
    )
    router = GatingRouter()
    plan2 = router.decide(bundle, 2)
    plan12 = router.decide(bundle, 12)
    assert plan2.hud_max_sheets == 2 and plan12.hud_max_sheets == 12, "HUD 配置未接线"
    long_meta = replace(meta, duration=2400)
    long_plan = router.decide(replace(bundle, meta=long_meta), 4)
    assert long_plan.need_hud, "40 分钟实况应继续出 HUD"

    sheets = [SheetInfo(index=i, kind="hud", start=i * 2, end=i * 2 + 2, cells=4, interval=2.0) for i in range(1, 7)]
    sheets += [SheetInfo(index=7, kind="scene", start=0, end=20, cells=6), SheetInfo(index=8, kind="scene", start=20, end=40, cells=6)]
    evidence = EvidenceBundle(sheets=sheets, frame_times=[0, 10, 20, 30, 40], hud_interval=2.0, sample_note="单测")
    picks = fallback_image_indices(evidence)
    assert picks == [1, 3, 6, 7], f"降级应优先 HUD，实际 {picks}"
    subset = evidence.subset(set(picks))
    assert [s.index for s in subset.sheets] == [1, 2, 3, 4]
    prompt = build_analysis_input(bundle, plan2, evidence=subset)
    assert "图 1~3：底栏操作栏时间带" in prompt, "HUD 未排在图像索引前面"
    assert "图 4（场景 1/1）" in prompt, "降级后的场景编号未重排"

    assert hud_crop_fraction(True) == (0.0, 0.75, 1.0, 0.25)
    portrait_filter = build_filter_complex(1.0, 1.0, True)
    assert "ih*0.25" in portrait_filter and "ih*0.75" in portrait_filter

    with tempfile.TemporaryDirectory() as tmp:
        frame = Path(tmp) / "portrait.jpg"
        img = Image.new("RGB", (200, 400), (20, 20, 20))
        img.paste((180, 40, 40), (0, 300, 200, 400))
        img.save(frame)
        hud_crop = crop_hud_frame(frame)
        assert hud_crop and hud_crop.exists()
        with Image.open(hud_crop) as out:
            assert out.width == 400 and 180 <= out.height <= 220, out.size

        # 横屏底栏：高边缘密度在右侧时，应选右侧窗口而不是固定居中
        band = Image.new("RGB", (1000, 100), (0, 0, 0))
        for x in range(620, 900, 10):
            band.paste((255, 255, 255), (x, 20, x + 4, 80))
        window = choose_hud_window(band)
        assert window[0] >= 0.5, f"自适应窗口未选中右侧: {window}"

        center_band = Image.new("RGB", (1000, 100), (0, 0, 0))
        for x in range(350, 650, 10):
            center_band.paste((255, 255, 255), (x, 20, x + 4, 80))
        center_window = choose_hud_window(center_band)
        assert center_window[0] < 0.5, f"自适应窗口未选中居中: {center_window}"

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        images = []
        for i in range(4):
            path = tmp_path / f"letterbox_{i}.jpg"
            img = Image.new("RGB", (120, 120), (0, 0, 0))
            img.paste((220, 220, 220), (0, 30, 120, 90))
            img.save(path)
            images.append(path)
        box = estimate_content_box(images)
        assert box is not None, "纯 Pillow 黑边检测失败"
        assert 0.2 <= box[1] <= 0.35 and 0.65 <= box[3] <= 0.8, box

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        thumb_dir = tmp_path / "thumbs"
        hud_dir = tmp_path / "hud"
        thumb_dir.mkdir()
        for i in range(2):
            (thumb_dir / f"{i + 1:05d}.jpg").write_bytes(b"x")
        meta_path = tmp_path / "sample_meta.json"
        meta_path.write_text(
            '{"duration": 100, "thumb_fps": 1.0, "hud_interval": 2.0, "thumb_count": 2, '
            '"hud_count": 0, "source_width": 1920, "source_height": 1080, '
            '"portrait": false, "crop_version": 3}',
            encoding="utf-8",
        )
        assert _load_cache(meta_path, 100, 1.0, 2.0, thumb_dir, hud_dir, 1920, 1080, False)
        assert not _load_cache(meta_path, 100, 1.0, 2.0, thumb_dir, hud_dir, 1280, 720, False)

    subtitles = [
        CleanedSubtitleParagraph(start_seconds=i * 20.0, end_seconds=i * 20.0 + 10, text=f"第{i}段")
        for i in range(500)
    ]
    subtitle_prompt = build_analysis_input(replace(bundle, subtitles=subtitles), plan2)
    assert "均匀抽样 240 段" in subtitle_prompt
    assert "第0段" in subtitle_prompt and "第497段" in subtitle_prompt, "长字幕后半段被截断"

    cad = RawVideoMeta(
        bvid="BV1cad", aid=1, cid=2, title="AutoCAD 2026 教程", desc="软件教程",
        up_name="up", up_uid=1, duration=600, pubdate=0, tname="科技", tags=["软件"],
    )
    assert not is_gameplay(cad), "AutoCAD 教程被误判为游戏实况"
    assert extract_page_number("https://www.bilibili.com/video/BV1x?p=3") == 3
    assert with_page("BV1x", 3) == "BV1x?p=3"
    print("  -> HUD 配置生效、降级保 HUD、竖屏/缓存/长字幕/分P/误判均正常")


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
    test_evidence_regressions()
    print("=" * 52)
    print("[PASS] All perception tests passed successfully!")
    print("=" * 52)


if __name__ == "__main__":
    run_all_tests()
