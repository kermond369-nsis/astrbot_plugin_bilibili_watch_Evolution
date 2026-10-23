"""使用有道高清真人多语言发音实测硅基流动 SenseVoiceSmall (日语、韩语、中文)。"""

import json
import os
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.asr.engine_openai_compatible import OpenAICompatibleASREngine

SILICONFLOW_KEY = os.environ.get("SILICONFLOW_API_KEY", "")

TEST_CASES = [
    {
        "lang_name": "日语 (常用问候)",
        "lang_code": "jap",
        "text": "こんにちは",
        "expected": "こんにちは (你好)",
    },
    {
        "lang_name": "日语句子 (感谢语)",
        "lang_code": "jap",
        "text": "ありがとうございます",
        "expected": "ありがとうございます (非常感谢)",
    },
    {
        "lang_name": "二次元常用日语 (辛苦了/退勤)",
        "lang_code": "jap",
        "text": "お疲れ様でした",
        "expected": "お疲れ様でした (辛苦了)",
    },
    {
        "lang_name": "二次元常用日语 (请多关照)",
        "lang_code": "jap",
        "text": "よろしくお願いします",
        "expected": "よろしくお願いします (请多指教)",
    },
    {
        "lang_name": "韩语 (常用问候)",
        "lang_code": "ko",
        "text": "안녕하세요",
        "expected": "안녕하세요 (你好)",
    },
    {
        "lang_name": "韩语 (感谢语)",
        "lang_code": "ko",
        "text": "감사합니다",
        "expected": "감사합니다 (谢谢)",
    },
]


def fetch_pronunciation_mp3(text: str, lang: str) -> bytes:
    encoded = urllib.parse.quote(text)
    url = f"https://dict.youdao.com/dictvoice?audio={encoded}&le={lang}"
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read()


def run():
    print("==================================================")
    print("硅基流动 FunAudioLLM/SenseVoiceSmall 多语言实测")
    print("==================================================")

    if not SILICONFLOW_KEY:
        print("[提示] 请先配置环境变量 SILICONFLOW_API_KEY 后再运行实时多语言测试。")
        return

    engine = OpenAICompatibleASREngine(
        api_key=SILICONFLOW_KEY,
        base_url="https://api.siliconflow.cn/v1",
        model="FunAudioLLM/SenseVoiceSmall",
    )

    for item in TEST_CASES:
        print(f"\n>>> 测试语种: {item['lang_name']}")
        print(f"    真实音频内容: {item['expected']}")

        # 1. 下载标准高清发音 MP3
        try:
            mp3_bytes = fetch_pronunciation_mp3(item["text"], item["lang_code"])
            print(f"    音频获取成功，大小: {len(mp3_bytes)} 字节")
        except Exception as exc:
            print(f"    [ERR] 音频获取失败: {exc}")
            continue

        # 2. 调用 SiliconFlow 转录
        try:
            start_t = time.monotonic()
            res = engine.transcribe(
                audio_bytes=mp3_bytes,
                audio_format="mp3",
                language="auto",
                timeout=20,
            )
            elapsed = time.monotonic() - start_t

            print(f"    [PASS] 转录完成 (接口耗时: {elapsed:.2f}s)")
            print(f"    [识别结果] -> \"{res.text}\"")
        except Exception as exc:
            print(f"    [FAILED] 接口调用异常: {exc}")

    print("\n==================================================")
    print("多语言 ASR 实测完毕！")
    print("==================================================")


if __name__ == "__main__":
    run()
