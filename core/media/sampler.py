"""视频采样与证据图编排。

一次 ffmpeg 解码同时产出两类证据：
1. 低分辨率缩略图（场景联表用，控制体积）；
2. 底栏 HUD 裁片（操作栏时间带用，保留原生分辨率）。

相比旧实现「每个时间点起一个 ffmpeg 进程重新连流 seek」，这里只连一次、只解码一遍，
长视频的抽帧耗时与带宽都会明显下降。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from astrbot.api import logger

THUMB_HEIGHT = 480
THUMB_MAX_FRAMES = 300
HUD_MAX_WIDTH = 1920
# 横屏先取整条底栏，合成时再按边缘密度选左/中/右窗口，避免只赌右下角
HUD_FRACTION = (0.0, 0.815, 1.0, 0.185)
HUD_PORTRAIT_FRACTION = (0.0, 0.75, 1.0, 0.25)  # 竖屏底部通栏
HUD_CROP_VERSION = 3  # 裁切算法变更时递增，使旧帧缓存失效
HUD_WINDOW_CANDIDATES = (
    (0.0, 0.485),    # 左侧
    (0.15, 0.75),    # 居中
    (0.515, 1.0),    # 右侧
)


def hud_crop_fraction(portrait: bool) -> tuple[float, float, float, float]:
    return HUD_PORTRAIT_FRACTION if portrait else HUD_FRACTION


@dataclass
class Sample:
    index: int
    time: float
    thumb: Path | None = None
    hud: Path | None = None


@dataclass
class SamplingResult:
    thumbs: list[Sample] = field(default_factory=list)
    huds: list[Sample] = field(default_factory=list)
    thumb_fps: float = 1.0
    hud_interval: float = 0.0
    duration: float = 0.0
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.thumbs)

    @property
    def thumb_count(self) -> int:
        return len(self.thumbs)

    @property
    def hud_count(self) -> int:
        return len(self.huds)


def plan_thumb_fps(duration: float, max_frames: int = THUMB_MAX_FRAMES) -> float:
    """按片长限制缩略图总数，返回抽样帧率（0.2~2 fps）。"""
    duration = max(1.0, float(duration or 0))
    fps = max_frames / duration
    fps = max(0.2, min(2.0, fps))
    return round(fps, 4)


def _is_allowed_url(url: str) -> bool:
    return isinstance(url, str) and url.startswith(("http://", "https://"))


def _int_even(value: float) -> int:
    return max(2, int(value) // 2 * 2)


def build_filter_complex(
    thumb_fps: float,
    hud_fps: float,
    portrait: bool = False,
) -> str:
    """构造双输出滤镜图。"""
    thumb = f"[0:v]fps={thumb_fps},scale=-2:{THUMB_HEIGHT}:flags=bilinear[t]"
    x, y, w, h = hud_crop_fraction(portrait)
    crop = (
        f"crop=trunc(iw*{w}/2)*2:trunc(ih*{h}/2)*2:"
        f"trunc(iw*{x}/2)*2:trunc(ih*{y}/2)*2"
    )
    hud = f"[0:v]fps={hud_fps},{crop},scale='min(iw,{HUD_MAX_WIDTH})':-2:flags=lanczos[h]"
    return f"{thumb};{hud}"


def _run(cmd: list[str], timeout: int) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, check=False, capture_output=True, timeout=timeout)
        tail = (proc.stderr or b"").decode("utf-8", "replace").strip()
        if len(tail) > 800:
            tail = tail[-800:]
        return proc.returncode, tail
    except subprocess.TimeoutExpired:
        return -9, "ffmpeg 超时"
    except Exception as exc:  # pragma: no cover - 环境异常
        return -1, str(exc)


def _load_cache(
    meta_path: Path,
    duration: float,
    thumb_fps: float,
    hud_interval: float,
    thumb_dir: Path,
    hud_dir: Path,
    source_width: int = 0,
    source_height: int = 0,
    portrait: bool = False,
) -> SamplingResult | None:
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if abs(float(data.get("duration", -1)) - duration) > 1.0:
        return None
    if abs(float(data.get("thumb_fps", 0)) - thumb_fps) > 0.01:
        return None
    if abs(float(data.get("hud_interval", 0)) - hud_interval) > 0.01:
        return None
    if int(data.get("crop_version", 0)) != HUD_CROP_VERSION:
        return None
    if int(data.get("source_width", 0)) != int(source_width):
        return None
    if int(data.get("source_height", 0)) != int(source_height):
        return None
    if bool(data.get("portrait", False)) != bool(portrait):
        return None
    thumbs = sorted(thumb_dir.glob("*.jpg"))
    huds = sorted(hud_dir.glob("*.jpg")) if hud_dir.exists() else []
    if not thumbs or len(thumbs) < max(1, int(data.get("thumb_count", 0)) - 2):
        return None
    thumb_samples = [
        Sample(index=i + 1, time=round(i / thumb_fps, 2), thumb=path)
        for i, path in enumerate(thumbs)
    ]
    hud_samples = (
        [
            Sample(index=i + 1, time=round(i / (1.0 / hud_interval), 2), hud=path)
            for i, path in enumerate(huds)
        ]
        if huds and hud_interval > 0
        else []
    )
    return SamplingResult(
        thumbs=thumb_samples,
        huds=hud_samples,
        thumb_fps=thumb_fps,
        hud_interval=hud_interval,
        duration=duration,
    )


def sample_stream(
    video_url: str,
    duration: float,
    out_dir: Path,
    ffmpeg_exe: str = "ffmpeg",
    referer: str = "https://www.bilibili.com/",
    user_agent: str = "",
    cookie: str = "",
    thumb_fps: float | None = None,
    hud_interval: float = 0.0,
    portrait: bool = False,
    source_width: int = 0,
    source_height: int = 0,
    force: bool = False,
) -> SamplingResult:
    """单次解码采样。hud_interval<=0 表示不要 HUD 裁片。

    采样参数一致时直接复用上次的帧目录（同一视频反复追问/重复观看时省一次解码）。
    """
    if not _is_allowed_url(video_url):
        return SamplingResult(duration=duration, error="非 http(s) 流地址")

    duration = max(0.0, float(duration or 0))
    thumb_fps = float(thumb_fps or plan_thumb_fps(duration))
    out_dir = Path(out_dir)
    thumb_dir = out_dir / "thumbs"
    hud_dir = out_dir / "hud"
    meta_path = out_dir / "sample_meta.json"

    if not force and meta_path.exists():
        cached = _load_cache(
            meta_path,
            duration,
            thumb_fps,
            hud_interval,
            thumb_dir,
            hud_dir,
            source_width=source_width,
            source_height=source_height,
            portrait=portrait,
        )
        if cached is not None:
            return cached

    thumb_dir.mkdir(parents=True, exist_ok=True)
    for old in list(thumb_dir.glob("*.jpg")) + list(hud_dir.glob("*.jpg") if hud_dir.exists() else []):
        old.unlink(missing_ok=True)

    headers = f"Referer: {referer}\r\n"
    if user_agent:
        headers += f"User-Agent: {user_agent}\r\n"
    if cookie:
        headers += f"Cookie: {cookie}\r\n"

    hud_fps = round(1.0 / hud_interval, 4) if hud_interval > 0 else 0.0
    base = [ffmpeg_exe, "-hide_banner", "-loglevel", "error", "-nostdin", "-headers", headers, "-i", video_url, "-an"]

    if hud_fps > 0:
        hud_dir.mkdir(parents=True, exist_ok=True)
        cmd = base + [
            "-filter_complex", build_filter_complex(thumb_fps, hud_fps, portrait),
            "-map", "[t]", "-q:v", "4", str(thumb_dir / "%05d.jpg"),
            "-map", "[h]", "-q:v", "3", str(hud_dir / "%05d.jpg"),
            "-y",
        ]
        timeout = int(max(180, duration * 2.2))
        code, err = _run(cmd, timeout)
        if code != 0:
            logger.warning(f"HUD 双输出采样失败({code})，回退为仅缩略图: {err}")
            for old in hud_dir.glob("*.jpg"):
                old.unlink(missing_ok=True)
            hud_fps = 0.0

    if hud_fps <= 0:
        cmd = base + [
            "-vf", f"fps={thumb_fps},scale=-2:{THUMB_HEIGHT}:flags=bilinear",
            "-q:v", "4", str(thumb_dir / "%05d.jpg"), "-y",
        ]
        timeout = int(max(120, duration * 2.0))
        code, err = _run(cmd, timeout)
        if code != 0:
            return SamplingResult(duration=duration, thumb_fps=thumb_fps, error=err)

    thumbs = sorted(thumb_dir.glob("*.jpg"))
    huds = sorted(hud_dir.glob("*.jpg")) if hud_dir.exists() else []
    if not thumbs:
        return SamplingResult(duration=duration, thumb_fps=thumb_fps, error="未产出缩略图")

    thumb_samples = [
        Sample(index=i + 1, time=round(i / thumb_fps, 2), thumb=path)
        for i, path in enumerate(thumbs)
    ]
    hud_samples = []
    if huds and hud_fps > 0:
        hud_samples = [
            Sample(index=i + 1, time=round(i / hud_fps, 2), hud=path)
            for i, path in enumerate(huds)
        ]

    result = SamplingResult(
        thumbs=thumb_samples,
        huds=hud_samples,
        thumb_fps=thumb_fps,
        hud_interval=round(1.0 / hud_fps, 2) if hud_fps else 0.0,
        duration=duration,
    )
    try:
        meta_path.write_text(
            json.dumps(
                {
                    "duration": duration,
                    "thumb_fps": thumb_fps,
                    "hud_interval": result.hud_interval,
                    "thumb_count": len(thumb_samples),
                    "hud_count": len(hud_samples),
                    "source_width": int(source_width),
                    "source_height": int(source_height),
                    "portrait": bool(portrait),
                    "crop_version": HUD_CROP_VERSION,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return result

def analyze_changes(thumbs: Sequence[Path]) -> tuple[list[float], list[float]]:
    """返回 (变化分, 亮度) 两个等长序列。

    变化分用 64x36 灰度图的平均绝对差，够稳且几乎不耗 CPU。
    """
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return [0.0] * len(thumbs), [0.0] * len(thumbs)

    diffs: list[float] = []
    brightness: list[float] = []
    prev = None
    for path in thumbs:
        try:
            with Image.open(path) as img:
                gray = img.convert("L").resize((64, 36))
                if prev is None:
                    diffs.append(0.0)
                else:
                    diff = sum(abs(a - b) for a, b in zip(gray.getdata(), prev.getdata())) / (64 * 36)
                    diffs.append(round(float(diff), 2))
                brightness.append(round(sum(gray.getdata()) / (64 * 36), 1))
                prev = gray.copy()
        except Exception:
            diffs.append(0.0)
            brightness.append(0.0)
    return diffs, brightness


def _load_font(size: int):
    try:
        from PIL import ImageFont

        try:
            return ImageFont.load_default(size=size)
        except TypeError:  # Pillow < 10
            return ImageFont.load_default()
    except Exception:  # pragma: no cover
        return None


def estimate_content_box(images: Sequence[Path], sample_limit: int = 12) -> tuple[int, int, int, int] | None:
    """估计画面有效区域（去掉上下/左右黑边）。

    取若干帧缩到 64x36，统计「在绝大多数帧里都接近纯黑」的行/列，作为 letterbox 边。
    返回 (left, top, right, bottom) 的归一化比例；估计失败返回 None。
    """
    try:
        from PIL import Image
    except Exception:  # pragma: no cover
        return None

    paths = [p for p in images if Path(p).exists()][:sample_limit]
    if len(paths) < 3:
        return None
    dark_hits = [[0] * 64 for _ in range(36)]
    frame_count = 0
    for path in paths:
        try:
            with Image.open(path) as img:
                pixels = list(img.convert("L").resize((64, 36)).getdata())
        except Exception:
            continue
        frame_count += 1
        for row in range(36):
            base = row * 64
            for col in range(64):
                if pixels[base + col] < 12:
                    dark_hits[row][col] += 1
    if frame_count < 3:
        return None

    rows_dark = []
    for row in range(36):
        dark_cols = sum(1 for col in range(64) if dark_hits[row][col] / frame_count >= 0.9)
        rows_dark.append(dark_cols / 64 >= 0.9)
    cols_dark = []
    for col in range(64):
        dark_rows = sum(1 for row in range(36) if dark_hits[row][col] / frame_count >= 0.9)
        cols_dark.append(dark_rows / 36 >= 0.9)

    top = 0
    while top < 36 and rows_dark[top]:
        top += 1
    bottom = 36
    while bottom > top and rows_dark[bottom - 1]:
        bottom -= 1
    left = 0
    while left < 64 and cols_dark[left]:
        left += 1
    right = 64
    while right > left and cols_dark[right - 1]:
        right -= 1
    if right - left < 8 or bottom - top < 8:
        return None
    return (left / 64, top / 36, right / 64, bottom / 36)


def _edge_profile(image) -> list[float]:
    """返回底栏图的逐列水平边缘强度（96 列，已归一化）。"""
    gray = image.convert("L").resize((96, 16))
    pixels = list(gray.getdata())
    profile: list[float] = []
    for x in range(95):
        total = 0
        for y in range(16):
            base = y * 96 + x
            total += abs(pixels[base + 1] - pixels[base])
        profile.append(total / 16.0)
    # 3 列滑动平均，避免单列噪声
    smooth = []
    for i in range(len(profile)):
        lo = max(0, i - 1)
        hi = min(len(profile), i + 2)
        smooth.append(sum(profile[lo:hi]) / max(1, hi - lo))
    return smooth


def _best_window(profiles: Sequence[Sequence[float]]) -> tuple[float, float]:
    if not profiles:
        return (0.15, 0.75)
    count = len(profiles[0])
    avg = [
        sum(profile[i] for profile in profiles) / len(profiles)
        for i in range(count)
    ]
    best = HUD_WINDOW_CANDIDATES[1]
    best_score = -1.0
    for start, end in HUD_WINDOW_CANDIDATES:
        lo = int(start * count)
        hi = max(lo + 1, int(end * count))
        score = sum(avg[lo:hi]) / (hi - lo)
        if score > best_score:
            best_score = score
            best = (start, end)
    # 画面几乎无边缘（纯色/黑屏）时用居中窗口兜底
    if best_score < 0.8:
        return (0.15, 0.75)
    return best


def choose_hud_window(image) -> tuple[float, float]:
    """根据底栏边缘密度选择横向窗口（左/中/右）。"""
    return _best_window([_edge_profile(image)])


def choose_hud_window_for_images(images: Sequence[object]) -> tuple[float, float]:
    return _best_window([_edge_profile(image) for image in images])


def _crop_window(image, window: tuple[float, float]):
    width, height = image.size
    left = int(width * window[0])
    right = int(width * window[1])
    if right - left < 8:
        return image
    return image.crop((left, 0, right, height))


def crop_hud_frame(frame_path: Path, portrait: bool | None = None) -> Path | None:
    """从整帧裁出底栏 UI（横屏右下角 / 竖屏底部通栏），放大 2 倍。"""
    try:
        from PIL import Image

        with Image.open(frame_path) as img:
            width, height = img.size
            if portrait is None:
                portrait = height > width
            small = img.convert("L").resize((64, 36))
            pixels = list(small.getdata())
            content_rows = []
            for row in range(36):
                values = pixels[row * 64 : (row + 1) * 64]
                mean = sum(values) / 64
                var = sum((v - mean) ** 2 for v in values) / 64
                if mean > 12 or var ** 0.5 > 6:
                    content_rows.append(row)
            top = content_rows[0] if content_rows else 0
            bottom = content_rows[-1] + 1 if content_rows else 36
            content_top = int(height * top / 36)
            content_bottom = int(height * bottom / 36)
            content_height = max(1, content_bottom - content_top)
            fx, fy, fw, fh = hud_crop_fraction(portrait)
            box = (
                int(width * fx),
                max(content_top, content_top + int(content_height * fy)),
                int(width * min(1.0, fx + fw)),
                min(content_bottom, content_top + int(content_height * (fy + fh))),
            )
            band = img.crop(box)
            window = (0.0, 1.0) if portrait else choose_hud_window(band)
            crop = _crop_window(band, window)
            crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
            out = Path(frame_path).with_name(Path(frame_path).stem + "_hud.jpg")
            crop.convert("RGB").save(out, quality=90)
            return out
    except Exception:
        return None


def _crop_box(image, box: tuple[float, float, float, float]):
    width, height = image.size
    left = int(width * box[0])
    top = int(height * box[1])
    right = int(width * box[2])
    bottom = int(height * box[3])
    if right - left < 8 or bottom - top < 8:
        return image
    return image.crop((left, top, right, bottom))


def _fit(image, box: tuple[int, int]):
    from PIL import ImageOps

    return ImageOps.contain(image, box, method=1)


def compose_contact_sheet(
    samples: Sequence[Sample],
    out_path: Path,
    cols: int = 3,
    rows: int = 2,
    cell: tuple[int, int] = (640, 360),
) -> Path | None:
    """把缩略图拼成 16:9 联表，每格左上角标注序号与时间。"""
    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover
        return None

    items = [s for s in samples if s.thumb and Path(s.thumb).exists()]
    if not items:
        return None
    cell_w, cell_h = cell
    label_h = 22
    canvas = Image.new("RGB", (cols * cell_w, rows * (cell_h + label_h)), (12, 12, 12))
    draw = ImageDraw.Draw(canvas)
    font = _load_font(16)

    for slot, sample in enumerate(items[: cols * rows]):
        col, row = slot % cols, slot // cols
        x0, y0 = col * cell_w, row * (cell_h + label_h)
        draw.rectangle([x0, y0, x0 + cell_w, y0 + label_h], fill=(0, 0, 0))
        draw.text((x0 + 6, y0 + 3), f"{slot + 1:02d}  {format_time(sample.time)}", fill=(255, 214, 64), font=font)
        try:
            with Image.open(sample.thumb) as img:
                fitted = _fit(img.convert("RGB"), (cell_w - 4, cell_h - 4))
                canvas.paste(fitted, (x0 + (cell_w - fitted.width) // 2, y0 + label_h + (cell_h - fitted.height) // 2))
        except Exception:
            continue

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=88)
    return out_path


def compose_hud_sheet(
    samples: Sequence[Sample],
    out_path: Path,
    cols: int = 4,
    rows: int = 8,
    cell_width: int = 400,
    portrait: bool = False,
) -> Path | None:
    """把底栏 HUD 裁片按时间顺序拼成「操作栏时间带」。

    先估计并裁掉画面黑边，再按实际宽高比决定格子高度，避免把一半格子浪费在黑边上。
    """
    try:
        from PIL import Image, ImageDraw
    except Exception:  # pragma: no cover
        return None

    items = [s for s in samples if s.hud and Path(s.hud).exists()][: cols * rows]
    if not items:
        return None

    box = estimate_content_box([Path(s.hud) for s in items])
    loaded = []
    for sample in items:
        try:
            with Image.open(sample.hud) as img:
                image = img.convert("RGB")
                if box:
                    image = _crop_box(image, box)
                loaded.append((sample, image))
        except Exception:
            continue
    if not loaded:
        return None

    if not portrait:
        window = choose_hud_window_for_images([image for _, image in loaded])
        loaded = [(sample, _crop_window(image, window)) for sample, image in loaded]

    ratios = [image.width / max(1, image.height) for _, image in loaded]
    ratios.sort()
    aspect = ratios[len(ratios) // 2] if ratios else 4.6
    cell_h = int(max(46, min(120, cell_width / max(1.0, aspect))))
    label_h = 16
    canvas = Image.new("RGB", (cols * cell_width, rows * (cell_h + label_h)), (8, 8, 8))
    draw = ImageDraw.Draw(canvas)
    font = _load_font(13)

    for slot, (sample, image) in enumerate(loaded):
        col, row = slot % cols, slot // cols
        x0, y0 = col * cell_width, row * (cell_h + label_h)
        draw.rectangle([x0, y0, x0 + cell_width, y0 + label_h], fill=(0, 0, 0))
        draw.text((x0 + 4, y0 + 1), format_time(sample.time), fill=(255, 214, 64), font=font)
        fitted = _fit(image, (cell_width - 2, cell_h - 2))
        canvas.paste(
            fitted,
            (x0 + (cell_width - fitted.width) // 2, y0 + label_h + (cell_h - fitted.height) // 2),
        )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, quality=90)
    return out_path


def format_time(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def cleanup_old_frames(root: Path, keep_bvid: str, max_dirs: int = 8) -> None:
    """只保留最近少量视频的帧目录，避免磁盘无限增长。"""
    root = Path(root)
    if not root.exists():
        return
    dirs = [d for d in root.iterdir() if d.is_dir()]
    dirs.sort(key=lambda d: d.stat().st_mtime, reverse=True)
    kept = 0
    for d in dirs:
        if d.name == keep_bvid:
            kept += 1
            continue
        if kept < max_dirs:
            kept += 1
            continue
        for path in sorted(d.rglob("*"), reverse=True):
            try:
                path.unlink() if path.is_file() else path.rmdir()
            except Exception:
                pass
        try:
            d.rmdir()
        except Exception:
            pass
