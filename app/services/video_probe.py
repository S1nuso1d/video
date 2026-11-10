"""Deterministic media metadata extraction using FFprobe.

This module records measurable properties. It deliberately does not turn most
technical observations into automatic accept/reject decisions; project-specific
criteria and the decision engine own that policy.
"""

import json
import logging
import math
import shutil
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class VideoProbeError(RuntimeError):
    """The downloaded file cannot be reliably inspected as a video."""


class FFprobeUnavailableError(VideoProbeError):
    """FFprobe is not installed or cannot be started."""


@dataclass(frozen=True)
class VideoProbeResult:
    metadata: dict[str, Any]
    findings: list[dict[str, Any]]


def _as_float(value: Any) -> float | None:
    if value in (None, "", "N/A"):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _as_int(value: Any) -> int | None:
    if value in (None, "", "N/A"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_frame_rate(value: Any) -> float | None:
    if not isinstance(value, str) or not value or value == "N/A":
        return None
    try:
        rate = float(Fraction(value))
    except (ValueError, ZeroDivisionError, TypeError, OverflowError):
        return None
    return rate if math.isfinite(rate) and rate > 0 else None


def _resolve_ffprobe_binary() -> str:
    configured = settings.ffprobe_binary
    resolved = shutil.which(configured)
    if resolved:
        return resolved
    if Path(configured).is_file():
        return str(Path(configured))
    raise FFprobeUnavailableError(
        "FFprobe не найден. Установите FFmpeg и добавьте ffprobe в PATH."
    )


def _run_ffprobe(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise VideoProbeError("Скачанный видеофайл не найден.")

    command = [
        _resolve_ffprobe_binary(),
        "-v",
        "error",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(path),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, int(settings.ffprobe_timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VideoProbeError("Превышено время технической проверки видео (FFprobe).") from exc
    except OSError as exc:
        raise FFprobeUnavailableError(
            "Не удалось запустить FFprobe. Проверьте установку FFmpeg."
        ) from exc

    if completed.returncode != 0:
        logger.warning(
            "FFprobe failed for %s (exit=%s): %s",
            path.name,
            completed.returncode,
            (completed.stderr or "").strip()[-500:],
        )
        raise VideoProbeError(
            "FFprobe не смог прочитать видеофайл. Файл может быть повреждён или иметь неподдерживаемый формат."
        )

    try:
        payload = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise VideoProbeError("FFprobe вернул некорректные метаданные.") from exc

    if not isinstance(payload, dict):
        raise VideoProbeError("FFprobe вернул неожиданный формат метаданных.")
    return payload


def probe_video(path: Path) -> VideoProbeResult:
    """Extract normalized video/audio metadata and evidence-backed observations."""
    payload = _run_ffprobe(path)
    streams = payload.get("streams")
    if not isinstance(streams, list):
        streams = []

    video_streams = [
        item for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "video"
    ]
    audio_streams = [
        item for item in streams
        if isinstance(item, dict) and item.get("codec_type") == "audio"
    ]
    if not video_streams:
        raise VideoProbeError("В загруженном файле не обнаружена видеодорожка.")

    video = video_streams[0]
    format_info = payload.get("format")
    if not isinstance(format_info, dict):
        format_info = {}

    duration_seconds = _as_float(video.get("duration"))
    if duration_seconds is None:
        duration_seconds = _as_float(format_info.get("duration"))
    if duration_seconds is None or duration_seconds <= 0:
        raise VideoProbeError("Не удалось определить корректную длительность видео.")

    average_fps = _parse_frame_rate(video.get("avg_frame_rate"))
    nominal_fps = _parse_frame_rate(video.get("r_frame_rate"))
    fps = average_fps or nominal_fps

    file_size = path.stat().st_size
    metadata: dict[str, Any] = {
        "probe": "ffprobe",
        "duration_seconds": round(duration_seconds, 3),
        "file_size_bytes": file_size,
        "format_name": format_info.get("format_name"),
        "container_bit_rate": _as_int(format_info.get("bit_rate")),
        "video": {
            "codec": video.get("codec_name"),
            "profile": video.get("profile"),
            "width": _as_int(video.get("width")),
            "height": _as_int(video.get("height")),
            "pixel_format": video.get("pix_fmt"),
            "average_frame_rate": round(average_fps, 3) if average_fps is not None else None,
            "nominal_frame_rate": round(nominal_fps, 3) if nominal_fps is not None else None,
            "frame_rate": round(fps, 3) if fps is not None else None,
            "bit_rate": _as_int(video.get("bit_rate")),
            "frame_count": _as_int(video.get("nb_frames")),
            "sample_aspect_ratio": video.get("sample_aspect_ratio"),
            "display_aspect_ratio": video.get("display_aspect_ratio"),
        },
        "has_audio": bool(audio_streams),
        "audio_streams": [
            {
                "codec": stream.get("codec_name"),
                "sample_rate": _as_int(stream.get("sample_rate")),
                "channels": _as_int(stream.get("channels")),
                "channel_layout": stream.get("channel_layout"),
                "bit_rate": _as_int(stream.get("bit_rate")),
                "duration_seconds": _as_float(stream.get("duration")),
            }
            for stream in audio_streams
        ],
        "stream_count": len(streams),
    }

    findings: list[dict[str, Any]] = []
    if duration_seconds < 5.0:
        findings.append(
            {
                "code": "video_under_5_seconds",
                "severity": "critical",
                "source": "ffprobe",
                "confidence": "high",
                "description": "The measured video duration is under 5 seconds.",
                "evidence": {
                    "duration_seconds": round(duration_seconds, 3),
                    "threshold_seconds": 5.0,
                },
            }
        )

    if not audio_streams:
        findings.append(
            {
                "code": "audio_stream_missing",
                "severity": "warning",
                "source": "ffprobe",
                "confidence": "high",
                "description": (
                    "No audio stream was found. Whether this is disqualifying "
                    "depends on the active project specification."
                ),
                "evidence": {"audio_stream_count": 0},
            }
        )

    if fps is None:
        findings.append(
            {
                "code": "frame_rate_unavailable",
                "severity": "warning",
                "source": "ffprobe",
                "confidence": "high",
                "description": "A reliable frame rate could not be extracted from the video stream.",
                "evidence": {
                    "average_frame_rate": video.get("avg_frame_rate"),
                    "nominal_frame_rate": video.get("r_frame_rate"),
                },
            }
        )

    return VideoProbeResult(metadata=metadata, findings=findings)
