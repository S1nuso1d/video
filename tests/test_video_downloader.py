import os
from pathlib import Path

import pytest

from app.core.config import Settings
from app.services import video_downloader


def test_default_download_format_does_not_cap_source_resolution() -> None:
    settings = Settings(_env_file=None)
    assert settings.video_format == "bv+ba/b"


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

    class FailedProcess:
        returncode = 1

        def communicate(self, timeout=None):
            return "ERROR: source unavailable", None

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        video_downloader.subprocess,
        "Popen",
        lambda *args, **kwargs: FailedProcess(),
    )

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



def test_download_stops_process_and_cleans_temp_dir_on_cancellation(
    tmp_path: Path, monkeypatch
) -> None:
    from app.services.cancellation import AuditCancelled

    monkeypatch.setattr(video_downloader.settings, "video_temp_root", str(tmp_path))
    monkeypatch.setattr(video_downloader.shutil, "which", lambda _: "yt-dlp")

    class RunningProcess:
        def __init__(self):
            self.returncode = None
            self.terminated = False

        def communicate(self, timeout=None):
            self.returncode = self.returncode if self.returncode is not None else 0
            return "", None

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    process = RunningProcess()
    monkeypatch.setattr(
        video_downloader.subprocess,
        "Popen",
        lambda *args, **kwargs: process,
    )
    checks = 0

    def cancel_on_second_checkpoint():
        nonlocal checks
        checks += 1
        if checks >= 2:
            raise AuditCancelled("cancelled in test")

    with pytest.raises(AuditCancelled):
        video_downloader.download_video(
            "https://example.com/video",
            "audit-cancel",
            cancel_check=cancel_on_second_checkpoint,
        )

    assert process.terminated is True
    assert list(tmp_path.iterdir()) == []
