"""可视化设置页后端（字段规格 / 校验 / 缓存）的离线单测。

不依赖 AstrBot 运行时：只测纯函数与文件操作。
"""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.web.cache import cleanup_cache, summarize_cache
from core.web.spec import (
    ALL_FIELDS,
    FIELD_MAP,
    apply_defaults,
    mask_tail,
    public_values,
    spec_payload,
    validate_values,
)


def test_spec_matches_schema():
    print(">>> [测试 1] 页面字段规格与 _conf_schema.json 一致...")
    schema = json.loads((PROJECT_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    spec_keys = set(FIELD_MAP)
    assert spec_keys == set(schema), (
        f"页面与 schema 漂移：缺少 {sorted(set(schema) - spec_keys)}，多余 {sorted(spec_keys - set(schema))}"
    )
    for key, item in schema.items():
        field = FIELD_MAP[key]
        expected_kind = {
            "bool": "bool",
            "int": "int",
            "string": "string" if "secret" not in item else "secret",
            "text": "text",
            "select": "select",
        }[item["type"]]
        # 本项目的 string + options 就是下拉框；长文本在页面上用 textarea 渲染
        if item["type"] == "string" and item.get("options"):
            expected_kind = "select"
        if key == "ack_template":
            expected_kind = "text"
        assert field.kind == expected_kind, f"{key}: spec={field.kind} schema={item['type']}"
        if field.kind == "select":
            spec_options = [value for value, _ in field.options]
            assert set(spec_options) == set(item.get("options") or []), f"{key} 选项不一致"
        if field.kind in ("bool", "int", "string", "text"):
            assert field.default == item.get("default"), f"{key} 默认值不一致: {field.default} vs {item.get('default')}"
    print(f"  -> {len(spec_keys)} 个字段全部对齐")


def test_validation():
    print(">>> [测试 2] 白名单校验与类型转换...")
    values, errors = validate_values({"unknown_key": 1})
    assert errors and not values, "未知键应被拒绝"

    values, errors = validate_values({"user_daily_quota": "30"}, {"user_daily_quota": 20})
    assert not errors and values["user_daily_quota"] == 30

    values, errors = validate_values({"user_daily_quota": 99999})
    assert errors and "user_daily_quota" not in values, "越界应被拒绝"

    values, errors = validate_values({"quota_exempt_admins": "false"}, {"quota_exempt_admins": True})
    assert not errors and values["quota_exempt_admins"] is False

    values, errors = validate_values({"asr_provider": "openai"})
    assert errors, "非法枚举应被拒绝"

    values, errors = validate_values({"mimo_api_key": ""}, {"mimo_api_key": "sk-old"})
    assert not errors and values == {}, "空密钥应表示保持原值"

    values, errors = validate_values({"mimo_api_key": "__clear__"})
    assert not errors and values == {"mimo_api_key": ""}, "清除密钥失败"

    values, errors = validate_values({"mimo_api_key": "sk-new"})
    assert not errors and values["mimo_api_key"] == "sk-new"

    values, errors = validate_values({"ack_template": "  "})
    assert errors and "ack_template" not in values, "空白模板应被拒绝"
    print("  -> 未知键/越界/枚举/密钥保留与清除均正确")


def test_public_values_and_defaults():
    print(">>> [测试 3] 密钥脱敏与恢复默认...")
    current = {
        "mimo_api_key": "sk-secret",
        "sessdata": "",
        "user_daily_quota": 5,
        "ack_enabled": False,
        "gameplay_mode": "on",
    }
    values, secrets = public_values(current)
    assert "mimo_api_key" not in values and "sessdata" not in values
    assert secrets["mimo_api_key"] is True and secrets["sessdata"] is False
    assert values["user_daily_quota"] == 5

    defaults = apply_defaults(current)
    assert "mimo_api_key" not in defaults, "恢复默认不应清空密钥"
    assert "sessdata" not in defaults
    assert defaults["user_daily_quota"] == 20

    only = apply_defaults(current, ["ack_enabled"])
    assert only == {"ack_enabled": True}
    assert mask_tail("abcdef123456") == "****3456"
    assert mask_tail("") == ""
    print("  -> 脱敏、默认值跳过密钥均正确")


def test_cache_summary_and_cleanup():
    print(">>> [测试 4] 缓存统计与安全清理...")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "frames" / "BV1").mkdir(parents=True)
        (root / "frames" / "BV1" / "a.jpg").write_bytes(b"x" * 100)
        (root / "frames" / "BV1" / "b.jpg").write_bytes(b"x" * 50)
        (root / "transcripts").mkdir()
        (root / "transcripts" / "BV1.json").write_text("{}", encoding="utf-8")
        (root / "credentials.json").write_text("{}", encoding="utf-8")
        outside = root.parent / "outside_secret.txt"
        outside.write_text("keep", encoding="utf-8")

        summary = summarize_cache(root)
        assert summary["total_bytes"] == 150 + 2
        assert summary["frame_videos"] == 1
        assert summary["transcript_count"] == 1

        result = cleanup_cache(root, "transcripts")
        assert result["removed_files"] == 1
        assert not (root / "transcripts").exists()
        assert (root / "frames" / "BV1" / "a.jpg").exists(), "误删截图"
        assert (root / "credentials.json").exists(), "误删凭据"

        result = cleanup_cache(root, "all")
        assert result["removed_files"] >= 2
        assert not (root / "frames").exists()
        assert (root / "credentials.json").exists(), "all 清理误删凭据"
        assert outside.exists()

        try:
            cleanup_cache(root, "../..")
        except ValueError:
            pass
        else:
            raise AssertionError("非法 target 应被拒绝")
    print("  -> 统计/分类清理/凭据保护/非法 target 均正确")


def test_spec_payload_shape():
    print(">>> [测试 5] 分组与字段序列化...")
    payload = spec_payload()
    assert len(payload) == 6, len(payload)
    keys = [field["key"] for group in payload for field in group["fields"]]
    assert len(keys) == len(ALL_FIELDS)
    assert all("label" in field and "kind" in field for group in payload for field in group["fields"])
    print(f"  -> {len(payload)} 组 / {len(keys)} 字段")


def run_all_tests():
    print("=" * 52)
    print("开始执行可视化设置页后端离线验证...")
    print("=" * 52)
    test_spec_matches_schema()
    test_validation()
    test_public_values_and_defaults()
    test_cache_summary_and_cleanup()
    test_spec_payload_shape()
    print("=" * 52)
    print("[PASS] Web settings tests passed successfully!")
    print("=" * 52)


if __name__ == "__main__":
    run_all_tests()
