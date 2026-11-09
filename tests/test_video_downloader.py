import os
from pathlib import Path

import pytest

from app.services import video_downloader


def test_validate_video_url_rejects_non_http_urls() -> None:
    with pytest.raises(video_downloader.VideoDownloadError):
        video_downloader.validate_video_url("file:///secret/video.mp4")


def test_create_and_cleanup_temp_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(video_downloader.settings, "video_temp_root", str(tmp_path))
    work_dir = video_downloader.create_audit_temp_dir("audit/with unsafe chars")
    (work_dir / "video.mp4").write_bytes(b"test")

    assert work_dir.exists()
    video_downloader.cleanup_audit_temp_dir(work_dir)
    assert not work_dir.exists()


def test_download_failure_cleans_directory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(video_downloader.settings, "video_temp_root", str(tmp_path))
    monkeypatch.setattr(video_downloader.shutil, "which", lambda _: "yt-dlp")

    class Result:
        returncode = 1
        stderr = "ERROR: source unavailable"
        stdout = ""

    monkeypatch.setattr(video_downloader.subprocess, "run", lambda *args, **kwargs: Result())

    with pytest.raises(video_downloader.VideoDownloadError):
        video_downloader.download_video("https://example.com/video", "audit-1")

    assert list(tmp_path.iterdir()) == []


def test_cleanup_abandoned_temp_dirs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(video_downloader.settings, "video_temp_root", str(tmp_path))
    old_dir = tmp_path / "old"
    old_dir.mkdir()
    os.utime(old_dir, (1_000, 1_000))

    monkeypatch.setattr(video_downloader.time, "time", lambda: 10_000)

    assert video_downloader.cleanup_abandoned_temp_dirs(older_than_seconds=100) == 1
    assert not old_dir.exists()
