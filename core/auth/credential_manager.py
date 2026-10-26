"""B 站登录凭据持久化与热加载管理器（Credential Manager）。

负责：
1. 读写插件数据目录（`data/plugin_data/astrbot_plugin_bilibili_watch`）下的 `credentials.json`；
2. 凭据优先级：已扫码的本地持久化凭据 > 独立配置文件 config.json > 环境变量；
3. 提供热更新与注销清除接口，无需重启插件。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from astrbot.api import logger

CREDENTIAL_FILE = "credentials.json"


class CredentialManager:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.file_path = self.data_dir / CREDENTIAL_FILE
        self._cached: dict[str, str] = {}
        self.reload()

    def reload(self):
        """从磁盘重新加载凭据"""
        if self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding="utf-8")
                self._cached = json.loads(content)
            except Exception as exc:
                logger.error(f"加载凭据文件失败: {exc}")
                self._cached = {}
        else:
            self._cached = {}

    def get_sessdata(self) -> str:
        return self._cached.get("sessdata", "").strip()

    def get_refresh_token(self) -> str:
        return self._cached.get("refresh_token", "").strip()

    def has_valid_credential(self) -> bool:
        return bool(self.get_sessdata())

    def save(self, credentials: dict[str, str]):
        """保存新凭据到磁盘"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cached = dict(credentials)
        try:
            self.file_path.write_text(json.dumps(self._cached, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info(f"B 站登录凭据已成功持久化至 {self.file_path}")
        except Exception as exc:
            logger.error(f"保存凭据文件失败: {exc}")

    def clear(self):
        """清除本地凭据"""
        self._cached = {}
        if self.file_path.exists():
            try:
                self.file_path.unlink()
                logger.info("B 站登录凭据文件已清除")
            except Exception as exc:
                logger.warning(f"删除凭据文件失败: {exc}")
