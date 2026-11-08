"""插件数据目录的缓存统计与清理（纯文件操作，无 AstrBot 依赖）。"""

from __future__ import annotations

import shutil
from pathlib import Path

# 允许清理的子目录白名单；credentials.json 等不在其中
CACHE_DIRS = {
    "frames": "截图 / 场景联表 / HUD 时间带",
    "transcripts": "字幕 / ASR 原文缓存",
    "qrcode": "扫码登录二维码",
    "cache": "ASR 临时音频",
}


def _dir_stats(path: Path) -> tuple[int, int]:
    if not path.exists():
        return 0, 0
    total = 0
    files = 0
    for child in path.rglob("*"):
        try:
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
                files += 1
        except OSError:
            continue
    return total, files


def summarize_cache(root: Path) -> dict:
    root = Path(root)
    items = []
    total_bytes = 0
    total_files = 0
    for name, desc in CACHE_DIRS.items():
        size, files = _dir_stats(root / name)
        total_bytes += size
        total_files += files
        items.append({"name": name, "desc": desc, "bytes": size, "files": files})
    frame_videos = 0
    frames_dir = root / "frames"
    if frames_dir.exists():
        frame_videos = sum(1 for child in frames_dir.iterdir() if child.is_dir())
    transcript_count = 0
    transcripts_dir = root / "transcripts"
    if transcripts_dir.exists():
        transcript_count = sum(1 for child in transcripts_dir.glob("*.json"))
    return {
        "items": items,
        "total_bytes": total_bytes,
        "total_files": total_files,
        "frame_videos": frame_videos,
        "transcript_count": transcript_count,
        "root": str(root),
    }


def _safe_rmtree(path: Path, root: Path) -> tuple[int, int]:
    """删除 root 下的一个子目录，返回 (释放字节, 删除文件数)。"""
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        if not resolved.is_relative_to(root_resolved) or resolved == root_resolved:
            return 0, 0
    except OSError:
        return 0, 0
    size, files = _dir_stats(path)
    shutil.rmtree(path, ignore_errors=True)
    return size, files


def cleanup_cache(root: Path, target: str = "all") -> dict:
    """清理指定缓存。target: frames | transcripts | qrcode | cache | all。"""
    root = Path(root)
    target = (target or "all").strip().lower()
    if target != "all" and target not in CACHE_DIRS:
        raise ValueError(f"未知缓存类型: {target}")
    targets = list(CACHE_DIRS) if target == "all" else [target]
    freed = 0
    removed = 0
    for name in targets:
        size, files = _safe_rmtree(root / name, root)
        freed += size
        removed += files
    return {"freed_bytes": freed, "removed_files": removed, "target": target}
