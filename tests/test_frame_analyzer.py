from dataclasses import dataclass

import numpy as np

from app.services import frame_analyzer
from app.services.frame_analyzer import FrameSample


def test_black_frame_segment_is_reported_with_time_evidence(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_black_min_duration_seconds", 1.0)
    samples = [
        FrameSample(0.0, 0.0, 1.0),
        FrameSample(0.5, 2.0, 0.995),
        FrameSample(1.0, 1.0, 0.99),
        FrameSample(1.5, 90.0, 0.05),
    ]

    findings = frame_analyzer._build_findings(samples, 0.5)

    assert len(findings) == 1
    assert findings[0]["code"] == "black_frame_segment"
    assert findings[0]["source"] == "opencv"
    assert findings[0]["evidence"]["start_time_seconds"] == 0.0
    assert findings[0]["evidence"]["end_time_seconds"] == 1.5
    assert findings[0]["evidence"]["sample_count"] == 3


def test_dark_scene_below_black_ratio_threshold_is_not_flagged(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_black_min_duration_seconds", 0.5)
    samples = [FrameSample(0.0, 8.0, 0.90), FrameSample(0.5, 8.0, 0.90)]

    assert frame_analyzer._build_findings(samples, 0.5) == []


def test_freeze_candidate_requires_sustained_low_motion(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_freeze_min_duration_seconds", 1.5)
    monkeypatch.setattr(frame_analyzer.settings, "cv_freeze_mean_diff_threshold", 1.0)
    monkeypatch.setattr(frame_analyzer.settings, "cv_freeze_changed_pixel_ratio", 0.01)
    samples = [
        FrameSample(0.0, 100.0, 0.0),
        FrameSample(1.0, 100.0, 0.0, 0.2, 0.001),
        FrameSample(2.0, 101.0, 0.0, 0.3, 0.002),
        FrameSample(3.0, 100.0, 0.0, 0.1, 0.001),
        FrameSample(4.0, 100.0, 0.0, 15.0, 0.2),
    ]

    findings = frame_analyzer._build_findings(samples, 1.0)

    assert len(findings) == 1
    assert findings[0]["code"] == "freeze_frame_suspected"
    assert findings[0]["confidence"] == "low"
    assert findings[0]["evidence"]["estimated_duration_seconds"] == 3.0
    assert findings[0]["evidence"]["sample_count"] == 4


def test_freeze_detector_does_not_duplicate_black_screen(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_freeze_min_duration_seconds", 0.5)
    samples = [
        FrameSample(0.0, 0.0, 1.0),
        FrameSample(1.0, 0.0, 1.0, 0.0, 0.0),
        FrameSample(2.0, 0.0, 1.0, 0.0, 0.0),
    ]

    findings = frame_analyzer._build_findings(samples, 1.0)

    assert [item["code"] for item in findings] == ["black_frame_segment"]


def test_blur_candidate_requires_a_sustained_low_focus_measure(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_blur_focus_threshold", 10.0)
    monkeypatch.setattr(frame_analyzer.settings, "cv_blur_min_duration_seconds", 1.0)
    samples = [
        FrameSample(0.0, 100.0, 0.0, blur_score=5.0),
        FrameSample(0.5, 100.0, 0.0, blur_score=6.0),
        FrameSample(1.0, 100.0, 0.0, blur_score=4.0),
        FrameSample(1.5, 100.0, 0.0, blur_score=80.0),
    ]

    findings = frame_analyzer._build_findings(samples, 0.5)

    blur = next(item for item in findings if item["code"] == "blur_suspected")
    assert blur["confidence"] == "low"
    assert blur["evidence"]["estimated_duration_seconds"] == 1.5
    assert blur["evidence"]["mean_focus_measure"] == 5.0


def test_scene_change_candidate_uses_adjacent_sample_evidence(monkeypatch):
    monkeypatch.setattr(frame_analyzer.settings, "cv_scene_change_threshold", 0.7)
    samples = [
        FrameSample(0.0, 80.0, 0.0, scene_change_score=None),
        FrameSample(0.5, 90.0, 0.0, scene_change_score=0.82),
        FrameSample(1.0, 90.0, 0.0, scene_change_score=0.2),
    ]

    findings = frame_analyzer._build_findings(samples, 0.5)

    assert len(findings) == 1
    assert findings[0]["code"] == "scene_change_suspected"
    assert findings[0]["evidence"]["start_time_seconds"] == 0.0
    assert findings[0]["evidence"]["end_time_seconds"] == 0.5


@dataclass
class _FakeCapture:
    frames: list
    fps: float = 2.0
    position: int = 0

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == _FakeCV2.CAP_PROP_FPS:
            return self.fps
        if prop == _FakeCV2.CAP_PROP_FRAME_COUNT:
            return len(self.frames)
        return 0.0

    def set(self, prop, value):
        if prop == _FakeCV2.CAP_PROP_POS_FRAMES:
            self.position = int(value)
            return True
        return False

    def read(self):
        if self.position >= len(self.frames):
            return False, None
        frame = self.frames[self.position]
        self.position += 1
        return True, frame

    def release(self):
        return None


class _FakeCV2:
    CAP_PROP_FPS = 1
    CAP_PROP_FRAME_COUNT = 2
    CAP_PROP_POS_FRAMES = 3
    COLOR_BGR2GRAY = 4
    CMP_LE = 5
    INTER_AREA = 6
    THRESH_BINARY = 7
    CV_64F = 8
    IMWRITE_JPEG_QUALITY = 9
    __version__ = "test"

    def __init__(self, frames):
        self.capture = _FakeCapture(frames)

    def VideoCapture(self, _path):
        return self.capture

    def resize(self, frame, _size, interpolation=None):
        return frame

    def cvtColor(self, frame, _code):
        return frame[:, :, 0]

    def mean(self, image):
        return (float(np.mean(image)), 0.0, 0.0, 0.0)

    def compare(self, image, threshold, _mode):
        return np.less_equal(image, threshold).astype(np.uint8) * 255

    def countNonZero(self, image):
        return int(np.count_nonzero(image))

    def absdiff(self, left, right):
        return np.abs(left.astype(np.int16) - right.astype(np.int16)).astype(np.uint8)

    def threshold(self, image, threshold, max_value, _mode):
        mask = (image > threshold).astype(np.uint8) * max_value
        return threshold, mask

    def Laplacian(self, image, _depth):
        return _FakeLaplacian(np.var(image))

    def imencode(self, _extension, _image, _options):
        return True, np.frombuffer(b"fake-jpeg", dtype=np.uint8)


@dataclass
class _FakeLaplacian:
    variance: float

    def var(self):
        return float(self.variance)


def test_analyze_video_frames_samples_with_a_bounded_timeline(tmp_path, monkeypatch):
    path = tmp_path / "synthetic.mp4"
    path.write_bytes(b"fake")
    dark = np.zeros((8, 8, 3), dtype=np.uint8)
    bright = np.full((8, 8, 3), 100, dtype=np.uint8)
    fake_cv2 = _FakeCV2([dark, dark, bright, bright])

    monkeypatch.setattr(frame_analyzer, "cv2", fake_cv2)
    monkeypatch.setattr(frame_analyzer.settings, "cv_sample_interval_seconds", 0.5)
    monkeypatch.setattr(frame_analyzer.settings, "cv_max_samples", 10)
    monkeypatch.setattr(frame_analyzer.settings, "cv_resize_width", 320)
    monkeypatch.setattr(frame_analyzer.settings, "cv_black_min_duration_seconds", 0.5)
    monkeypatch.setattr(frame_analyzer.settings, "cv_freeze_min_duration_seconds", 2.5)
    monkeypatch.setattr(frame_analyzer.settings, "cv_blur_min_duration_seconds", 2.0)
    monkeypatch.setattr(frame_analyzer.settings, "cv_evidence_max_frames", 4)
    monkeypatch.setattr(frame_analyzer.settings, "cv_evidence_width", 160)

    result = frame_analyzer.analyze_video_frames(path, duration_seconds=2.0)

    assert result.metadata["sampled_frames"] == 4
    assert result.metadata["evidence_frames"] > 0
    assert result.metadata["source_fps"] == 2.0
    assert result.metadata["analysis_resolution_width"] == 8
    assert any(item["code"] == "black_frame_segment" for item in result.findings)
    assert fake_cv2.capture.release is not None


def test_analyze_video_frames_rejects_missing_file(tmp_path):
    import pytest

    with pytest.raises(frame_analyzer.VideoFrameAnalysisError, match="не найден"):
        frame_analyzer.analyze_video_frames(tmp_path / "missing.mp4", 2.0)
