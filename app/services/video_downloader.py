"""Download and clean up source videos for one audit attempt."""

import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from app.core.config import settings
from app.services.cancellation import AuditCancelled


class VideoDownloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloadedVideo:
    path: Path
    work_dir: Path


def _safe_audit_id(audit_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_-]", "_", audit_id)
    return value[:128] or "audit"


def create_audit_temp_dir(audit_id: str) -> Path:
    root = Path(settings.video_temp_root)
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{_safe_audit_id(audit_id)}_", dir=root))


def cleanup_audit_temp_dir(work_dir: Path | None) -> None:
    if work_dir is not None:
        shutil.rmtree(work_dir, ignore_errors=True)


def cleanup_abandoned_temp_dirs(older_than_seconds: int = 7200) -> int:
    root = Path(settings.video_temp_root)
    if not root.exists():
        return 0
    cutoff = time.time() - max(0, older_than_seconds)
    removed = 0
    for path in root.iterdir():
        try:
            if path.is_dir() and path.stat().st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def validate_video_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise VideoDownloadError("Некорректная ссылка на видео.")


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return

    # On Windows yt-dlp can spawn ffmpeg for merging separate audio/video
    # streams. Terminate the whole process tree so cancellation does not leave
    # a detached ffmpeg process consuming CPU or holding the temporary file.
    process_id = getattr(process, "pid", None)
    if os.name == "nt" and process_id is not None:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process_id), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            return
        except (OSError, subprocess.SubprocessError):
            pass

    process.terminate()
    try:
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()


def download_video(
    url: str,
    audit_id: str,
    *,
    cancel_check: Callable[[], None] | None = None,
) -> DownloadedVideo:
    """Download one source video and remain responsive to cancellation requests."""
    validate_video_url(url)
    if shutil.which(settings.yt_dlp_binary) is None:
        raise VideoDownloadError(
            f"Не найден {settings.yt_dlp_binary}. Установите yt-dlp и добавьте его в PATH."
        )

    work_dir = create_audit_temp_dir(audit_id)
    output_template = str(work_dir / "video.%(ext)s")
    command = [
        settings.yt_dlp_binary,
        "--no-playlist",
        "--no-progress",
        "--quiet",
        "--no-warnings",
        "--concurrent-fragments",
        "8",
        "--max-filesize",
        str(settings.video_max_filesize_bytes),
        "-f",
        settings.video_format,
        "-o",
        output_template,
        url,
    ]
    process: subprocess.Popen | None = None
    try:
        if cancel_check is not None:
            cancel_check()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        deadline = time.monotonic() + max(
            1, int(settings.video_download_timeout_seconds)
        )
        output = ""
        while True:
            if cancel_check is not None:
                cancel_check()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise VideoDownloadError(
                    "Превышено время ожидания скачивания видео."
                )
            try:
                output, _ = process.communicate(timeout=min(1.0, remaining))
                break
            except subprocess.TimeoutExpired:
                continue

        if process.returncode != 0:
            details = (output or "").strip().splitlines()
            tail = details[-1] if details else "unknown error"
            raise VideoDownloadError(
                f"yt-dlp не смог скачать видео: {tail[:300]}"
            )

        if cancel_check is not None:
            cancel_check()

        files = sorted(path for path in work_dir.iterdir() if path.is_file())
        if not files:
            raise VideoDownloadError(
                "yt-dlp завершился без создания видеофайла."
            )

        video_path = max(files, key=lambda path: path.stat().st_size)
        if video_path.stat().st_size <= 0:
            raise VideoDownloadError("Скачанный видеофайл пуст.")

        return DownloadedVideo(path=video_path, work_dir=work_dir)
    except AuditCancelled:
        _stop_process(process)
        cleanup_audit_temp_dir(work_dir)
        raise
    except VideoDownloadError:
        _stop_process(process)
        cleanup_audit_temp_dir(work_dir)
        raise
    except OSError as exc:
        _stop_process(process)
        cleanup_audit_temp_dir(work_dir)
        raise VideoDownloadError("Не удалось запустить yt-dlp.") from exc
    except Exception:
        _stop_process(process)
        cleanup_audit_temp_dir(work_dir)
        raise
