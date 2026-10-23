"""真实 B 站数据抓取连通性测试。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.cleaner.pipeline import run_clean_pipeline
from core.client.bili_client import BiliClient
from core.gating.router import GatingRouter


def run():
    print("==================================================")
    print("开始 B 站真实接口测试 (BV号解析与清洗连通)...")
    print("==================================================")

    test_bv = "BV1xx411c7m9"  # Bad Apple 影绘经典视频，弹幕极其丰富
    client = BiliClient()

    print(f"1. 正在获取视频元数据: {test_bv} ...")
    meta = client.get_video_meta(test_bv)
    print(f"   -> 标题: {meta.title}")
    print(f"   -> UP主: {meta.up_name} (UID: {meta.up_uid})")
    print(f"   -> 时长: {meta.duration}秒, 播放: {meta.views}, 弹幕: {meta.danmaku}")

    print("2. 正在拉取弹幕列表...")
    danmaku_list = client.get_danmaku(meta.cid)
    print(f"   -> 成功获取 {len(danmaku_list)} 条弹幕！")

    print("3. 正在拉取评论区...")
    comments = client.get_comments(meta.aid, limit=10)
    print(f"   -> 成功获取 {len(comments)} 条热门/置顶评论！")

    print("4. 尝试获取字幕...")
    subs = client.get_subtitles(meta.bvid, meta.cid)
    print(f"   -> 提取到 {len(subs)} 行官方字幕 (若为0则进入无字幕/ASR分支)")

    print("5. 运行完整清洗流水线 (run_clean_pipeline)...")
    bundle = run_clean_pipeline(meta, subs, comments, danmaku_list)
    print(f"   -> 清洗后简介行数: {len(bundle.clean_desc.splitlines())}")
    print(f"   -> 弹幕高能波峰点: {len(bundle.high_energy_points)} 处")
    for p in bundle.high_energy_points[:3]:
        print(f"      * [{p.time_point}s] 强度 {p.intensity}x | 梗词: {p.top_keywords}")

    print("6. 运行门控路由判定 (GatingRouter)...")
    router = GatingRouter()
    plan = router.decide(bundle)
    print(f"   -> 门控规划模式: {plan.mode.value}")
    print(f"   -> 判定理由: {plan.reason}")
    print(f"   -> 是否需要截帧: {plan.need_frames}")
    if plan.need_frames:
        print(f"   -> 规划截帧时间戳: {plan.target_frame_timestamps}")

    print("==================================================")
    print("🎉 B 站真实抓取、清洗与门控端到端验证通过！")
    print("==================================================")


if __name__ == "__main__":
    run()
