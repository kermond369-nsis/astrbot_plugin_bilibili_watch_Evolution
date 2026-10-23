"""生成微型真实 WAV 并验证 MiMo ASR 识别。"""

import io
import math
import os
import struct
import sys
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.asr.engine_mimo import MimoASREngine


def make_valid_wav(duration_sec: float = 1.0, sample_rate: int = 16000) -> bytes:
    """生成一段包含微弱底噪或纯音的合法单声道 WAV 字节"""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)  # 单声道
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)

        # 写入 1 秒的低振幅 440Hz 纯音数据
        total_frames = int(sample_rate * duration_sec)
        samples = []
        for i in range(total_frames):
            val = int(500 * math.sin(2 * math.pi * 440 * (i / sample_rate)))
            samples.append(val)
        data = struct.pack(f"<{len(samples)}h", *samples)
        wf.writeframes(data)

    return buffer.getvalue()


def run():
    print("生成合法的 1 秒 WAV 音频...")
    wav_bytes = make_valid_wav(1.0)
    print(f"音频生成完成，大小: {len(wav_bytes)} 字节")

    key = os.environ.get("MIMO_API_KEY", "")
    if not key:
        print("[提示] 请先配置环境变量 MIMO_API_KEY 后再运行实时联调。")
        return
    engine = MimoASREngine(api_key=key)
    res = engine.transcribe(wav_bytes, audio_format="wav", language="zh", timeout=20)
    print(f"[SUCCESS] 小米 MiMo ASR 调用成功！")
    print(f"识别文本: '{res.text}'")
    print(f"音频耗时统计: {res.duration_seconds} 秒")
    print(f"完整响应: {res.raw_response}")


if __name__ == "__main__":
    run()
