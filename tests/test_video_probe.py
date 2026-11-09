import json
from pathlib import Path

import pytest

from app.services import video_probe


def _ffprobe_payload(*, duration="12.5", include_audio=True, fps="30000/1001"):
    streams = [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "profile": "High",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "avg_frame_rate": fps,
            "r_frame_rate": "30/1",
            "bit_rate": "4500000",
            "nb_frames": "375",
            "duration": duration,
            "sample_aspect_ratio": "1:1",
            "display_aspect_ratio": "16:9",
        }
    ]
    if include_audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": "aac",
                "sample_rate": "48000",
                "channels": 2,
                "channel_layout": "stereo",
                "bit_rate": "128000",
                "duration": duration,
            }
        )
    return {
        "streams": streams,
        "format": {
            "format_name": "mov,mp4,m4a,3gp,3g2,mj2",
            "duration": duration,
            "size": "12345",
            "bit_rate": "4628000",
        },
    }


def _mock_ffprobe(monkeypatch, payload, path: Path):
    class Completed:
        returncode = 0
        stdout = json.dumps(payload)
        stderr = ""

    monkeypatch.setattr(video_probe.shutil, "which", lambda _: "ffprobe")
    monkeypatch.setattr(video_probe.subprocess, "run", lambda *args, **kwargs: Completed())
    path.write_bytes(b"fake-video")


def test_probe_video_extracts_video_and_audio_metadata(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    _mock_ffprobe(monkeypatch, _ffprobe_payload(), path)

    result = video_probe.probe_video(path)

    assert result.metadata["duration_seconds"] == 12.5
    assert result.metadata["video"]["width"] == 1920
    assert result.metadata["video"]["height"] == 1080
    assert result.metadata["video"]["frame_rate"] == pytest.approx(29.97, rel=1e-3)
    assert result.metadata["has_audio"] is True
    assert result.metadata["audio_streams"][0]["sample_rate"] == 48000
    assert result.findings == []


def test_probe_video_records_short_video_as_measured_finding(tmp_path, monkeypatch):
    path = tmp_path / "short.mp4"
    _mock_ffprobe(monkeypatch, _ffprobe_payload(duration="4.2"), path)

    result = video_probe.probe_video(path)

    assert result.findings[0]["code"] == "video_under_5_seconds"
    assert result.findings[0]["severity"] == "critical"
    assert result.findings[0]["evidence"]["duration_seconds"] == 4.2


def test_probe_video_records_missing_audio_without_rejecting(tmp_path, monkeypatch):
    path = tmp_path / "silent.mp4"
    _mock_ffprobe(
        monkeypatch,
        _ffprobe_payload(include_audio=False),
        path,
    )

    result = video_probe.probe_video(path)

    assert result.metadata["has_audio"] is False
    assert result.findings[0]["code"] == "audio_stream_missing"
    assert result.findings[0]["severity"] == "warning"


def test_probe_video_handles_invalid_frame_rate(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    _mock_ffprobe(monkeypatch, _ffprobe_payload(fps="0/0"), path)

    result = video_probe.probe_video(path)

    assert result.metadata["video"]["frame_rate"] == 30.0
    assert not any(item["code"] == "frame_rate_unavailable" for item in result.findings)


def test_probe_video_rejects_file_without_video_stream(tmp_path, monkeypatch):
    path = tmp_path / "audio-only.mp4"
    _mock_ffprobe(
        monkeypatch,
        {"streams": [{"codec_type": "audio"}], "format": {"duration": "10"}},
        path,
    )

    with pytest.raises(video_probe.VideoProbeError, match="видеодорожка"):
        video_probe.probe_video(path)


def test_probe_video_reports_unavailable_binary(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video")
    monkeypatch.setattr(video_probe.shutil, "which", lambda _: None)
    monkeypatch.setattr(video_probe.settings, "ffprobe_binary", "ffprobe")

    with pytest.raises(video_probe.FFprobeUnavailableError):
        video_probe.probe_video(path)


def test_probe_video_reports_timeout(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video")
    monkeypatch.setattr(video_probe.shutil, "which", lambda _: "ffprobe")

    def timeout(*args, **kwargs):
        raise video_probe.subprocess.TimeoutExpired(cmd="ffprobe", timeout=1)

    monkeypatch.setattr(video_probe.subprocess, "run", timeout)

    with pytest.raises(video_probe.VideoProbeError, match="Превышено время"):
        video_probe.probe_video(path)


def test_probe_video_rejects_invalid_json(tmp_path, monkeypatch):
    path = tmp_path / "video.mp4"
    path.write_bytes(b"fake-video")

    class Completed:
        returncode = 0
        stdout = "not-json"
        stderr = ""

    monkeypatch.setattr(video_probe.shutil, "which", lambda _: "ffprobe")
    monkeypatch.setattr(video_probe.subprocess, "run", lambda *args, **kwargs: Completed())

    with pytest.raises(video_probe.VideoProbeError, match="некорректные"):
        video_probe.probe_video(path)
