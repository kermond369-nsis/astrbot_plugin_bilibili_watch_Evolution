"""真实 B 站游戏视频的画面证据链路联调（不调用 LLM）。

验证：元数据/标签 -> 字幕可信度 -> 门控（gameplay）-> 单次解码采样 -> 场景联表 + HUD 时间带。

用法：
    python tests/live/test_gameplay_evidence_live.py
    BILI_TEST_BVID=BV1MwxY63Eu9 python tests/live/test_gameplay_evidence_live.py

需要 ffmpeg（系统 PATH 或 FFMPEG_PATH），建议配置 SESSDATA（BILI_SESSDATA）以获取 1080P。
"""

import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cleaner.pipeline import run_clean_pipeline
from core.client.bili_client import BiliClient
from core.client.media_extractor import MediaExtractor
from core.gating.router import GatingRouter
from core.media.frame_plan import hud_interval, select_scene_times
from core.media.sampler import (
    analyze_changes,
    compose_contact_sheet,
    compose_hud_sheet,
    sample_stream,
)


def run() -> None:
    bvid = os.environ.get("BILI_TEST_BVID", "BV1MwxY63Eu9")
    sessdata = os.environ.get("BILI_SESSDATA", "").strip()
    client = BiliClient(sessdata=sessdata)

    print("1. 元数据与标签...")
    meta = client.get_video_meta(bvid)
    print(f"   {meta.title} | {meta.duration}s | tags={meta.tags}")

    print("2. 字幕/评论/弹幕...")
    track = client.get_subtitle_track(meta.bvid, meta.cid)
    comments = client.get_comments(meta.aid, 40)
    danmaku = client.get_danmaku(meta.cid)
    bundle = run_clean_pipeline(meta, track.items, comments, danmaku, subtitle_track=track)
    trust = bundle.subtitle_trust
    print(f"   字幕: {track.source}/{len(track.items)} -> 可信度 {trust.level if trust else 'none'}")
    print(f"   评论: {len(comments)} | 文字轴候选: {len(bundle.axis_comments)} | gameplay={bundle.gameplay}")

    print("3. 门控...")
    plan = GatingRouter().decide(bundle)
    print(f"   {plan.mode.value} gameplay={plan.gameplay} hud={plan.need_hud} frames={plan.target_frame_count}")
    assert plan.gameplay, "该视频未被识别为游戏实况；可用 BILI_TEST_BVID 换一个样本"

    print("4. 单次解码采样...")
    info = client.get_stream_info(meta.bvid, meta.cid)
    print(f"   stream {info.width}x{info.height} audio={'yes' if info.audio_url else 'no'}")
    headers = client.media_headers()
    workdir = PROJECT_ROOT / "tests" / "temp_frames" / meta.bvid
    gap = hud_interval(meta.duration, plan.hud_max_sheets, 32) if plan.need_hud else 0.0
    t0 = time.time()
    sampling = sample_stream(
        info.video_url,
        meta.duration,
        workdir,
        MediaExtractor(os.environ.get("FFMPEG_PATH", "")).get_ffmpeg_exe(),
        headers.get("Referer", ""),
        headers.get("User-Agent", ""),
        headers.get("Cookie", ""),
        None,
        gap,
    )
    print(f"   thumbs={sampling.thumb_count} huds={sampling.hud_count} in {time.time() - t0:.1f}s")
    assert sampling.ok, f"采样失败: {sampling.error}"

    print("5. 联表与 HUD 时间带...")
    changes, _ = analyze_changes([s.thumb for s in sampling.thumbs])
    picked = select_scene_times(
        [s.time for s in sampling.thumbs], changes, plan.target_frame_count, meta.duration
    )
    by_time = {round(s.time, 1): s for s in sampling.thumbs}
    chosen = [by_time.get(round(t, 1)) or min(sampling.thumbs, key=lambda s: abs(s.time - t)) for t in picked.times]
    sheets = []
    for no, start in enumerate(range(0, len(chosen), 6), start=1):
        path = compose_contact_sheet(chosen[start : start + 6], workdir / f"scene_{no:02d}.jpg")
        if path:
            sheets.append(path)
    for no, start in enumerate(range(0, len(sampling.huds), 32), start=1):
        path = compose_hud_sheet(sampling.huds[start : start + 32], workdir / f"hud_{no:02d}.jpg")
        if path:
            sheets.append(path)
    assert sheets, "没有生成任何证据图"
    for path in sheets:
        print(f"   {path.name} {path.stat().st_size // 1024}KB")
    print(f"[PASS] 生成 {len(sheets)} 张证据图：{workdir}")


if __name__ == "__main__":
    run()
