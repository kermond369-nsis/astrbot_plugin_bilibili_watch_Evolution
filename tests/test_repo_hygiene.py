"""仓库一致性检查（离线，无第三方依赖）：

1. main.py 的 PLUGIN_VERSION 与 metadata.yaml 的 version 一致；
2. _conf_schema.json 每个字段都有 description，且类型均为 AstrBot 支持的类型；
3. 仓库内不存在敏感文件（config.json / credentials.json / *.key）。
"""

import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

SUPPORTED_TYPES = {"int", "float", "bool", "string", "text", "list", "file", "object", "template_list", "dict"}
SENSITIVE_FILES = ("config.json", "credentials.json")


def test_version_consistency():
    main_src = (PROJECT_ROOT / "main.py").read_text(encoding="utf-8")
    match = re.search(r'^PLUGIN_VERSION\s*=\s*"([^"]+)"', main_src, re.MULTILINE)
    assert match, "main.py 中缺少 PLUGIN_VERSION"

    metadata = (PROJECT_ROOT / "metadata.yaml").read_text(encoding="utf-8")
    meta_match = re.search(r"^version:\s*(\S+)\s*$", metadata, re.MULTILINE)
    assert meta_match, "metadata.yaml 中缺少 version"

    assert match.group(1) == meta_match.group(1), (
        f"版本不一致: main.py={match.group(1)} metadata.yaml={meta_match.group(1)}"
    )
    print(f"  -> 版本一致: {match.group(1)}")


def test_config_schema_shape():
    schema = json.loads((PROJECT_ROOT / "_conf_schema.json").read_text(encoding="utf-8"))
    assert schema, "配置 schema 为空"
    for key, item in schema.items():
        assert "type" in item, f"{key} 缺少 type"
        assert item["type"] in SUPPORTED_TYPES, f"{key} 使用了 AstrBot 不支持的类型 {item['type']}"
        assert item.get("description"), f"{key} 缺少 description（面板标签）"
        assert len(item["description"]) <= 16, f"{key} 的 description 过长，面板可能截断"
    print(f"  -> schema 校验通过: {len(schema)} 个字段")


def test_no_sensitive_files():
    for name in SENSITIVE_FILES:
        assert not (PROJECT_ROOT / name).exists(), f"仓库根目录不允许出现 {name}"
    leaked = [p.name for p in PROJECT_ROOT.rglob("*.key")]
    assert not leaked, f"发现疑似密钥文件: {leaked}"
    print("  -> 未发现敏感文件")


if __name__ == "__main__":
    print(">>> 仓库一致性检查...")
    test_version_consistency()
    test_config_schema_shape()
    test_no_sensitive_files()
    print("[PASS] Repo hygiene checks passed.")
