"""视觉关键帧清洗与去重引擎（Frame Filter & Deduplication）。

纯算法实现，通过差分感知哈希 (dHash) 与纯色/黑屏检测：
1. 过滤片头片尾的黑屏、静态转场废帧；
2. 剔除连续相同场景下的高度相似帧（如定格手书、未翻页 PPT），避免浪费视觉 Token；
3. 输出真正具备信息量差异的关键画面集合。
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence


def compute_dhash(image_path: Path | str, hash_size: int = 8) -> int | None:
    """计算图片的差分感知哈希 (dHash 64-bit)。

    使用标准 PIL 库，将图片缩放到 (hash_size + 1, hash_size) 的灰度图，
    比对水平相邻像素的明暗差异。
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            # 缩放并转为灰度
            resized = img.convert("L").resize(
                (hash_size + 1, hash_size), Image.Resampling.BILINEAR
            )
            pixels = list(resized.getdata())

            diff = 0
            # 逐行比对相邻像素
            for row in range(hash_size):
                row_start = row * (hash_size + 1)
                for col in range(hash_size):
                    left = pixels[row_start + col]
                    right = pixels[row_start + col + 1]
                    diff = (diff << 1) | (1 if left > right else 0)

            return diff
    except Exception:
        return None


def hamming_distance(hash1: int, hash2: int) -> int:
    """计算两个 64 位整数哈希之间的汉明距离（不同位数的个数）。"""
    return bin(hash1 ^ hash2).count("1")


def is_blank_or_solid_frame(image_path: Path | str, threshold_std: float = 8.0) -> bool:
    """检测是否为纯黑屏、纯白屏或无细节纯色帧。"""
    try:
        from PIL import Image, ImageStat

        with Image.open(image_path) as img:
            gray = img.convert("L")
            stat = ImageStat.Stat(gray)
            mean_val = stat.mean[0]
            std_val = stat.stddev[0]

            # 极端暗（黑屏）或极端亮且几乎没有对比度（方差小）
            if mean_val < 10.0 or mean_val > 245.0 or std_val < threshold_std:
                return True
            return False
    except Exception:
        return False


def filter_and_deduplicate_frames(
    frame_tuples: Sequence[tuple[Path, float]],
    max_hamming_threshold: int = 6,
    max_keep: int = 8,
) -> list[tuple[Path, float]]:
    """对一组候选截图进行过滤与去重：

    :param frame_tuples: [(文件路径, 时间戳秒数), ...]
    :param max_hamming_threshold: 汉明距离阈值，<= 该值视为相同场景重复帧（64位中相差<=6位）
    :param max_keep: 最大保留的精选帧数量
    :return: 经过清洗去重后的精选关键帧序列 [(文件路径, 时间戳秒数), ...]
    """
    selected: list[tuple[Path, float]] = []
    selected_hashes: list[int] = []

    for path, timestamp in frame_tuples:
        if not Path(path).exists():
            continue

        # 1. 过滤黑屏/死色块
        if is_blank_or_solid_frame(path):
            continue

        # 2. 计算感知哈希
        img_hash = compute_dhash(path)
        if img_hash is None:
            continue

        # 3. 与已选帧进行相似度比对
        is_duplicate = False
        for prev_hash in selected_hashes:
            dist = hamming_distance(img_hash, prev_hash)
            if dist <= max_hamming_threshold:
                is_duplicate = True
                break

        if not is_duplicate:
            selected.append((path, timestamp))
            selected_hashes.append(img_hash)
            if len(selected) >= max_keep:
                break

    return selected
