"""门控路由引擎（Gating & Routing System）。

根据清洗提纯后的 CleanedVideoBundle 特征（字幕覆盖率、时间戳目录、弹幕高能点、时长），
做出自适应执行规划（GatingPlan）：
1. 模式 A (TEXT_OUTLINE): 教程/攻略/长口播视频 -> 纯文本总结，跳过截帧，秒级响应，零视觉Token消耗；
2. 模式 B (VISUAL_MEME): 二次元切片/手书/MMD/无字幕梗 -> 弹幕热点+视觉关键帧，子代理多模态观察；
3. 模式 C (HYBRID): 复杂长视频 -> 混合抽样。
"""

from __future__ import annotations

from core.models import CleanedVideoBundle, GatingPlan, ProcessingMode


class GatingRouter:
    def __init__(
        self,
        min_words_per_minute_for_text: int = 90,
        meme_danmaku_peaks_threshold: int = 2,
        default_meme_frame_count: int = 8,
    ):
        self.min_wpm_text = min_words_per_minute_for_text
        self.meme_dm_threshold = meme_danmaku_peaks_threshold
        self.default_meme_frame_count = default_meme_frame_count

    def decide(self, bundle: CleanedVideoBundle) -> GatingPlan:
        duration = max(1, bundle.meta.duration)
        duration_minutes = duration / 60.0
        words_per_minute = bundle.total_subtitle_words / max(0.5, duration_minutes)

        # 1. 优先判定：是否有野生目录君（时间戳目录）+ 充足字幕 -> 典型教程攻略类
        if bundle.has_outline and bundle.total_subtitle_words > 150:
            return GatingPlan(
                mode=ProcessingMode.TEXT_OUTLINE,
                reason=f"检测到完整时间戳目录（{len(bundle.timestamps_outline)}项）且字幕丰富，走纯文本极速纲要总结",
                need_frames=False,
                target_frame_count=0,
                system_prompt_type="tutorial_outline",
            )

        # 2. 字幕密度足够高（且时长在合理区间）-> 知识讲解/教程/剧情口播
        if words_per_minute >= self.min_wpm_text and duration > 60:
            return GatingPlan(
                mode=ProcessingMode.TEXT_OUTLINE,
                reason=f"字幕字速高（{words_per_minute:.1f}字/分），口播信息量充足，无需画面即可完整理解",
                need_frames=False,
                target_frame_count=0,
                system_prompt_type="speech_knowledge",
            )

        # 3. 画面与弹幕主导：二次元切片/手书/无字幕/台词碎
        # 规划目标截帧时间点：弹幕高能波峰时间点优先
        frame_timestamps = self._plan_frame_timestamps(bundle, self.default_meme_frame_count)

        return GatingPlan(
            mode=ProcessingMode.VISUAL_MEME,
            reason=f"字幕稀疏（{words_per_minute:.1f}字/分）或二次元高能视频，启用弹幕波峰({len(bundle.high_energy_points)}处)+关键帧视觉感知",
            need_frames=True,
            target_frame_count=len(frame_timestamps),
            target_frame_timestamps=frame_timestamps,
            system_prompt_type="visual_meme",
        )

    def _plan_frame_timestamps(self, bundle: CleanedVideoBundle, count: int) -> list[float]:
        """规划截帧时间戳：

        1. 优先采用弹幕高能波峰点（观众情绪最浓烈的时刻）；
        2. 结合目录时间点；
        3. 均匀分布保底；
        4. 规避片头片尾 5%（黑屏与片头尾Logo）。
        """
        duration = bundle.meta.duration
        if duration <= 5:
            return [1.0]

        start_safe = max(2.0, duration * 0.05)
        end_safe = min(duration - 2.0, duration * 0.95)

        candidates: list[float] = []

        # 1. 弹幕波峰
        for peak in bundle.high_energy_points:
            if start_safe <= peak.time_point <= end_safe:
                candidates.append(peak.time_point)

        # 2. 目录时间点
        for outline in bundle.timestamps_outline:
            if start_safe <= outline.seconds <= end_safe:
                candidates.append(outline.seconds)

        # 3. 均匀保底时间点
        step = (end_safe - start_safe) / max(1, count)
        for i in range(count):
            candidates.append(start_safe + step * (i + 0.5))

        # 去重并保持时间间隔 >= 4 秒
        selected: list[float] = []
        for ts in sorted(candidates):
            if all(abs(ts - existing) >= 4.0 for existing in selected):
                selected.append(round(ts, 1))
            if len(selected) >= count:
                break

        return sorted(selected)
