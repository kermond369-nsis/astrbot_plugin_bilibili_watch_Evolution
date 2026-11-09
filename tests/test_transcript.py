"""原文缓存与时间点解析的离线单测。"""

import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

STUB_ROOT = PROJECT_ROOT / "tests" / "stub"
if str(STUB_ROOT) not in sys.path:
    sys.path.insert(0, str(STUB_ROOT))

from core.perception.transcript_store import TranscriptLine, TranscriptRecord, TranscriptStore
from core.utils.timecode import format_time_point, parse_time_point


def test_timecode():
    print(">>> [测试 1] 时间点解析...")
    cases = {
        "90": 90.0,
        "01:30": 90.0,
        "1:02:03": 3723.0,
        "00:01:02.5": 62.5,
        "3分": None,
        "": None,
        "-5": None,
    }
    for raw, expected in cases.items():
        got = parse_time_point(raw)
        assert got == expected or (got is None and expected is None), (raw, got, expected)
    assert format_time_point(3723) == "01:02:03"
    assert format_time_point(90) == "01:30"
    print("  -> 秒数/mm:ss/hh:mm:ss 与非法输入均正确")


def test_transcript_store():
    print(">>> [测试 2] 原文缓存写入/检索/过期...")
    with tempfile.TemporaryDirectory() as tmp:
        store = TranscriptStore(Path(tmp), ttl_hours=72, max_entries=2)
        record = TranscriptRecord(
            bvid="BV1MwxY63Eu9",
            title="蓝甲赛特塔124层",
            source="asr",
            trust="ok",
            duration=374,
            lines=[
                TranscriptLine(0, 3, "开场先上水伊吹"),
                TranscriptLine(70, 74, "这里车白要开技能保命"),
                TranscriptLine(140, 145, "第二阶段转火"),
            ],
        )
        store.save(record)
        assert store.has("BV1MwxY63Eu9")
        assert len(store.search("BV1MwxY63Eu9")) == 3
        assert len(store.search("BV1MwxY63Eu9", keyword="车白")) == 1
        assert len(store.search("BV1MwxY63Eu9", start=60, end=80)) == 1
        assert store.search("BV1MwxY63Eu9", keyword="不存在") == []

        # 过期记录读取时应被删除
        path = store._path("BV1MwxY63Eu9")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["updated_at"] = 1.0
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert store.get("BV1MwxY63Eu9") is None, "过期记录未清理"
        assert not path.exists(), "过期文件未删除"

        # max_entries 淘汰
        for i in range(4):
            store.save(
                TranscriptRecord(
                    bvid=f"BV{i}",
                    lines=[TranscriptLine(0, 1, f"line {i}")],
                )
            )
        remaining = len(list(Path(tmp).glob("*.json")))
        assert remaining <= 2, f"max_entries 未生效: {remaining}"
    print("  -> 写入/关键词/时间范围/过期/上限均正常")


def run_all_tests():
    print("=" * 48)
    print("开始执行原文缓存与时间点解析单测...")
    print("=" * 48)
    test_timecode()
    test_transcript_store()
    print("=" * 48)
    print("[PASS] Transcript tests passed successfully!")
    print("=" * 48)


if __name__ == "__main__":
    run_all_tests()
