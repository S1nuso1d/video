"""Bounded OpenCV frame sampling and conservative visual observations.

The analyzer samples a limited number of resized frames across the full video.
Black-frame and freeze-like signals are evidence for human review, never a
standalone accept/reject decision.
"""

from __future__ import annotations

import base64
import logging
import math
import time

import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import settings

try:
    import cv2
except ImportError:  # Keep the rest of the application importable in minimal environments.
    cv2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class VideoFrameAnalysisError(RuntimeError):
    """The local video could not be sampled reliably."""


class OpenCVUnavailableError(VideoFrameAnalysisError):
    """OpenCV is not installed or cannot be imported."""


@dataclass(frozen=True)
class FrameAnalysisResult:
    metadata: dict[str, Any]
    findings: list[dict[str, Any]]


@dataclass(frozen=True)
class FrameSample:
    timestamp_seconds: float
    mean_luma: float
    dark_pixel_ratio: float
    motion_mean_difference: float | None = None
    motion_changed_pixel_ratio: float | None = None
    frame_index: int = -1
    blur_score: float | None = None
    scene_change_score: float | None = None

    @property
    def is_black_candidate(self) -> bool:
        return (
            self.dark_pixel_ratio >= settings.cv_black_pixel_ratio
            and self.mean_luma <= settings.cv_black_luma_threshold
        )


def _build_findings(
    samples: list[FrameSample],
    sample_interval_seconds: float,
    duration_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Convert sampled metrics into timestamped, deliberately conservative signals."""
    if not samples:
        return []

    interval = max(0.05, float(sample_interval_seconds))
    max_gap = interval * 1.8
    findings: list[dict[str, Any]] = []

    black_runs: list[list[FrameSample]] = []
    current_black_run: list[FrameSample] = []
    for sample in samples:
        if not sample.is_black_candidate:
            if current_black_run:
                black_runs.append(current_black_run)
                current_black_run = []
            continue
        if (
            current_black_run
            and sample.timestamp_seconds - current_black_run[-1].timestamp_seconds <= max_gap
        ):
            current_black_run.append(sample)
        else:
            if current_black_run:
                black_runs.append(current_black_run)
            current_black_run = [sample]
    if current_black_run:
        black_runs.append(current_black_run)

    for run in black_runs:
        start = run[0].timestamp_seconds
        end = run[-1].timestamp_seconds + interval
        if duration_seconds is not None and duration_seconds > 0:
            end = min(end, duration_seconds)
        duration = max(0.0, end - start)
        if duration < settings.cv_black_min_duration_seconds:
            continue
        findings.append(
            {
                "code": "black_frame_segment",
                "severity": "warning",
                "source": "opencv",
                "confidence": "medium",
                "description": (
                    "Sampling found a sustained near-black image region. "
                    "A dark scene may be intentional; verify the original frames."
                ),
                "evidence": {
                    "start_time_seconds": round(start, 3),
                    "end_time_seconds": round(end, 3),
                    "estimated_duration_seconds": round(duration, 3),
                    "sample_count": len(run),
                    "maximum_dark_pixel_ratio": round(
                        max(item.dark_pixel_ratio for item in run), 4
                    ),
                    "minimum_mean_luma": round(
                        min(item.mean_luma for item in run), 3
                    ),
                },
            }
        )

    # A freeze candidate requires low motion across multiple sampled intervals.
    # Completely black samples are excluded to avoid duplicating black-screen findings.
    active_freeze: dict[str, Any] | None = None
    freeze_runs: list[dict[str, Any]] = []
    for previous, current in zip(samples, samples[1:]):
        gap = current.timestamp_seconds - previous.timestamp_seconds
        low_motion = (
            0 < gap <= max_gap
            and not previous.is_black_candidate
            and not current.is_black_candidate
            and current.motion_mean_difference is not None
            and current.motion_changed_pixel_ratio is not None
            and current.motion_mean_difference
            <= settings.cv_freeze_mean_diff_threshold
            and current.motion_changed_pixel_ratio
            <= settings.cv_freeze_changed_pixel_ratio
        )
        if not low_motion:
            if active_freeze is not None:
                freeze_runs.append(active_freeze)
                active_freeze = None
            continue

        pair = {
            "start": previous.timestamp_seconds,
            "end": current.timestamp_seconds,
            "mean_differences": [current.motion_mean_difference],
            "changed_ratios": [current.motion_changed_pixel_ratio],
            "sample_count": 2,
        }
        if (
            active_freeze is not None
            and abs(active_freeze["end"] - pair["start"]) <= max(0.05, interval * 0.25)
        ):
            active_freeze["end"] = pair["end"]
            active_freeze["mean_differences"].extend(pair["mean_differences"])
            active_freeze["changed_ratios"].extend(pair["changed_ratios"])
            active_freeze["sample_count"] += 1
        else:
            if active_freeze is not None:
                freeze_runs.append(active_freeze)
            active_freeze = pair

    if active_freeze is not None:
        freeze_runs.append(active_freeze)

    for run in freeze_runs:
        duration = max(0.0, run["end"] - run["start"])
        if duration < settings.cv_freeze_min_duration_seconds:
            continue
        mean_differences = run["mean_differences"]
        changed_ratios = run["changed_ratios"]
        findings.append(
            {
                "code": "freeze_frame_suspected",
                "severity": "warning",
                "source": "opencv",
                "confidence": "low",
                "description": (
                    "Consecutive sampled frames were almost unchanged for a sustained "
                    "period. A static shot can be intentional; confirm against the source."
                ),
                "evidence": {
                    "start_time_seconds": round(run["start"], 3),
                    "end_time_seconds": round(run["end"], 3),
                    "estimated_duration_seconds": round(duration, 3),
                    "sample_count": run["sample_count"],
                    "mean_abs_pixel_difference": round(
                        sum(mean_differences) / len(mean_differences), 4
                    ),
                    "mean_changed_pixel_ratio": round(
                        sum(changed_ratios) / len(changed_ratios), 5
                    ),
                },
            }
        )

    findings.sort(
        key=lambda finding: finding.get("evidence", {}).get("start_time_seconds", 0)
    )
    return findings


def analyze_video_frames(
    path: Path,
    duration_seconds: float | None = None,
) -> FrameAnalysisResult:
    """Sample a bounded set of frames and detect black/freeze-like intervals."""
    if cv2 is None:
        raise OpenCVUnavailableError(
            "OpenCV не установлен. Установите зависимости проекта командой pip install -r requirements.txt."
        )
    if not path.is_file():
        raise VideoFrameAnalysisError("Скачанный видеофайл для анализа кадров не найден.")

    capture = None
    try:
        capture = cv2.VideoCapture(str(path))
        if not capture.isOpened():
            raise VideoFrameAnalysisError(
                "OpenCV не смог открыть видео для анализа кадров."
            )

        fps_value = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        fps = fps_value if math.isfinite(fps_value) and fps_value > 0 else None
        frame_count_value = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        frame_count = (
            int(frame_count_value)
            if math.isfinite(frame_count_value) and frame_count_value > 0
            else None
        )

        duration = (
            float(duration_seconds)
            if duration_seconds is not None
            and math.isfinite(float(duration_seconds))
            and float(duration_seconds) > 0
            else None
        )
        if frame_count is None and fps is not None and duration is not None:
            frame_count = max(1, int(math.ceil(fps * duration)))
        if duration is None and frame_count is not None and fps is not None:
            duration = frame_count / fps
        if frame_count is None or frame_count < 1:
            raise VideoFrameAnalysisError(
                "Не удалось определить количество кадров для ограниченной выборки."
            )

        max_samples = max(2, int(settings.cv_max_samples))
        configured_interval = max(0.05, float(settings.cv_sample_interval_seconds))
        effective_interval = max(
            configured_interval,
            (duration / max_samples) if duration is not None else configured_interval,
        )
        stride = max(1, int(math.ceil(frame_count / max_samples)))
        if fps is not None:
            stride = max(stride, int(round(fps * effective_interval)))
        stride = max(1, stride)

        frame_indices = list(range(0, frame_count, stride))[:max_samples]
        if (
            frame_count > 1
            and len(frame_indices) < max_samples
            and frame_indices[-1] != frame_count - 1
        ):
            frame_indices.append(frame_count - 1)

        resize_width = max(64, int(settings.cv_resize_width))
        samples: list[FrameSample] = []
        previous_gray = None
        analysis_started_at = time.monotonic()
        original_width = None
        original_height = None

        for frame_index in frame_indices:
            if time.monotonic() - analysis_started_at > settings.cv_analysis_timeout_seconds:
                raise VideoFrameAnalysisError(
                    "Превышено время ограниченного анализа видеокадров (OpenCV)."
                )
            positioned = capture.set(cv2.CAP_PROP_POS_FRAMES, float(frame_index))
            if not positioned and frame_index > 0:
                raise VideoFrameAnalysisError(
                    "OpenCV не поддерживает точную выборку кадров для этого видео."
                )
            ok, frame = capture.read()
            if not ok or frame is None:
                logger.debug(
                    "OpenCV could not read sampled frame %s from %s",
                    frame_index,
                    path.name,
                )
                continue

            height, width = frame.shape[:2]
            original_width = original_width or int(width)
            original_height = original_height or int(height)
            if width > resize_width:
                resized_height = max(1, int(round(height * resize_width / width)))
                frame = cv2.resize(
                    frame,
                    (resize_width, resized_height),
                    interpolation=cv2.INTER_AREA,
                )
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            pixel_count = int(gray.shape[0] * gray.shape[1])
            if pixel_count <= 0:
                continue
            mean_luma = float(cv2.mean(gray)[0])
            dark_mask = cv2.compare(
                gray,
                int(settings.cv_black_luma_threshold),
                cv2.CMP_LE,
            )
            dark_ratio = float(cv2.countNonZero(dark_mask) / pixel_count)

            motion_mean_difference = None
            motion_changed_pixel_ratio = None
            if previous_gray is not None and previous_gray.shape == gray.shape:
                difference = cv2.absdiff(previous_gray, gray)
                motion_mean_difference = float(cv2.mean(difference)[0])
                changed_mask = cv2.threshold(
                    difference,
                    int(settings.cv_freeze_pixel_delta),
                    255,
                    cv2.THRESH_BINARY,
                )[1]
                motion_changed_pixel_ratio = float(
                    cv2.countNonZero(changed_mask) / pixel_count
                )

            timestamp = (
                frame_index / fps
                if fps is not None
                else (
                    duration * frame_index / max(1, frame_count - 1)
                    if duration is not None
                    else 0.0
                )
            )
            samples.append(
                FrameSample(
                    timestamp_seconds=max(0.0, timestamp),
                    mean_luma=mean_luma,
                    dark_pixel_ratio=dark_ratio,
                    motion_mean_difference=motion_mean_difference,
                    motion_changed_pixel_ratio=motion_changed_pixel_ratio,
                )
            )
            previous_gray = gray

        if not samples:
            raise VideoFrameAnalysisError(
                "OpenCV не смог прочитать ни одного кадра из видео."
            )

        if duration is None:
            duration = samples[-1].timestamp_seconds + effective_interval
        actual_interval = (
            samples[1].timestamp_seconds - samples[0].timestamp_seconds
            if len(samples) > 1
            else effective_interval
        )
        actual_interval = (
            actual_interval
            if math.isfinite(actual_interval) and actual_interval > 0
            else effective_interval
        )

        metadata: dict[str, Any] = {
            "analyzer": "opencv",
            "opencv_version": getattr(cv2, "__version__", "unknown"),
            "duration_seconds": round(duration, 3),
            "source_fps": round(fps, 3) if fps is not None else None,
            "source_frame_count": frame_count,
            "source_width": original_width,
            "source_height": original_height,
            "sampled_frames": len(samples),
            "max_samples": max_samples,
            "sample_interval_seconds": round(actual_interval, 3),
            "sampling_was_capped": len(frame_indices) >= max_samples,
            "analysis_resolution_width": min(resize_width, original_width or resize_width),
        }
        return FrameAnalysisResult(
            metadata=metadata,
            findings=_build_findings(samples, actual_interval, duration),
        )
    except VideoFrameAnalysisError:
        raise
    except Exception as exc:
        logger.exception("OpenCV frame analysis failed for %s", path.name)
        raise VideoFrameAnalysisError(
            "Не удалось завершить компьютерный анализ видеокадров."
        ) from exc
    finally:
        if capture is not None:
            capture.release()
