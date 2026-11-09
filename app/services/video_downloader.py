import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings


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


def download_video(url: str, audit_id: str) -> DownloadedVideo:
    """Download one video into an isolated temporary directory using yt-dlp."""
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
        "--newline",
        "--max-filesize",
        str(settings.video_max_filesize_bytes),
        "-f",
        settings.video_format,
        "-o",
        output_template,
        url,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=settings.video_download_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        cleanup_audit_temp_dir(work_dir)
        raise VideoDownloadError("Превышено время ожидания скачивания видео.") from exc
    except OSError as exc:
        cleanup_audit_temp_dir(work_dir)
        raise VideoDownloadError("Не удалось запустить yt-dlp.") from exc

    if completed.returncode != 0:
        cleanup_audit_temp_dir(work_dir)
        details = (completed.stderr or completed.stdout or "").strip().splitlines()
        tail = details[-1] if details else "unknown error"
        raise VideoDownloadError(f"yt-dlp не смог скачать видео: {tail[:300]}")

    files = sorted(path for path in work_dir.iterdir() if path.is_file())
    if not files:
        cleanup_audit_temp_dir(work_dir)
        raise VideoDownloadError("yt-dlp завершился без создания видеофайла.")

    video_path = max(files, key=lambda path: path.stat().st_size)
    if video_path.stat().st_size <= 0:
        cleanup_audit_temp_dir(work_dir)
        raise VideoDownloadError("Скачанный видеофайл пуст.")

    return DownloadedVideo(path=video_path, work_dir=work_dir)
