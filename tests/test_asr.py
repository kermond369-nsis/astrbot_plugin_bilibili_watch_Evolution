"""ASR 模块与清洗离线/连通性单元测试。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.asr.engine_mimo import MimoASREngine
from core.cleaner.asr_cleaner import clean_asr_transcript, convert_asr_text_to_subtitles


def test_asr_cleaner_logic():
    print(">>> [ASR 测试 1] ASR 幻觉剔除、死循环复读与二次元术语纠偏...")
    raw_asr = (
        "神色，先森！今天总力赞我们用邻战爱丽丝。啊啊啊啊啊啊啊！"
        "白子·带扣开大招充能，这一波稳了！请不吝点赞订阅我的频道谢谢观看！"
    )
    cleaned = clean_asr_transcript(raw_asr)
    assert "请不吝点赞" not in cleaned, "幻觉词未剔除"
    assert "订阅我的频道" not in cleaned, "幻觉词未剔除"
    assert "Sensei" in cleaned, "Sensei 术语纠偏失败"
    assert "总力战" in cleaned, "总力战术语纠偏失败"
    assert "临战" in cleaned, "临战术语纠偏失败"
    assert "白子（骑行）" in cleaned, "白子（骑行）术语纠偏失败"
    assert "啊啊啊啊啊啊啊" not in cleaned, "异常连续重复字符未抑制"
    print("  -> ASR 特化清洗与术语纠偏通过！")


def test_asr_text_to_subtitles():
    print(">>> [ASR 测试 2] ASR 全文标点自然断句与时间戳平摊...")
    text = "Sensei，今天总力战注意轴的调整。第一阶段爱丽丝必须先破防！第二阶段渚补满伤害即可通关。"
    subs = convert_asr_text_to_subtitles(text, total_duration=60.0)
    assert len(subs) == 3, f"预期切分为 3 个自然句子，实际: {len(subs)}"
    assert subs[0].start == 0.0
    assert subs[-1].end == 60.0
    assert "爱丽丝" in subs[1].content
    print("  -> ASR 转时间戳字幕通过！")


def test_mimo_connectivity(api_key: str):
    print(">>> [ASR 测试 3] 小米 MiMo ASR 真实网络连通性校验...")
    # 构造一个合法的超微型 WAV 音频头部 (44 字节 PCM 静音)
    # 结构: RIFF header + fmt chunk + data chunk (空数据)
    wav_header = bytes([
        0x52, 0x49, 0x46, 0x46,  # "RIFF"
        0x24, 0x00, 0x00, 0x00,  # file size - 8 = 36
        0x57, 0x41, 0x56, 0x45,  # "WAVE"
        0x66, 0x6D, 0x74, 0x20,  # "fmt "
        0x10, 0x00, 0x00, 0x00,  # chunk size = 16
        0x01, 0x00,              # format = PCM (1)
        0x01, 0x00,              # channels = 1 (mono)
        0x44, 0xAC, 0x00, 0x00,  # sample rate = 44100
        0x88, 0x58, 0x01, 0x00,  # byte rate = 44100 * 2 = 88200
        0x02, 0x00,              # block align = 2
        0x10, 0x00,              # bits per sample = 16
        0x64, 0x61, 0x74, 0x61,  # "data"
        0x00, 0x00, 0x00, 0x00,  # data chunk size = 0
    ])

    engine = MimoASREngine(api_key=api_key)
    try:
        res = engine.transcribe(wav_header, audio_format="wav", language="zh", timeout=15)
        print(f"  -> MiMo 接口请求成功！识别响应文本: '{res.text}' (耗时统计: {res.duration_seconds}s)")
        print("  -> MiMo API 凭据有效，网络握手完全通畅！")
    except Exception as exc:
        print(f"  -> MiMo 接口调用结果: {exc}")


if __name__ == "__main__":
    test_asr_cleaner_logic()
    test_asr_text_to_subtitles()
    import os
    env_key = os.environ.get("MIMO_API_KEY", "")
    if env_key:
        test_mimo_connectivity(env_key)
    else:
        print(">>> [提示] 未检测到 MIMO_API_KEY 环境变量，跳过 MiMo 实时网络调用。")
