from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.audit import AuditRecord
from app.services import processor
from app.services.video_downloader import DownloadedVideo
from app.services.video_probe import VideoProbeResult
from app.services.frame_analyzer import FrameAnalysisResult, OpenCVUnavailableError


def _make_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'processor-technical.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(processor, "SessionLocal", factory)
    return engine, factory


def _create_processing_audit(factory, audit_id="audit-technical"):
    with factory() as db:
        db.add(
            AuditRecord(
                id=audit_id,
                youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
                video_id="abcdefghijk",
                content_type="unknown",
                status="processing",
                attempt_count=1,
                worker_id="test-worker",
                lease_token="test-lease",
            )
        )
        db.commit()


class _FakeReport:
    verdict = SimpleNamespace(value="candidate_accepted")
    summary = "Technical checks completed."
    detected_defects = []
    markup_comment = "Предварительно подходит."

    def model_dump(self, mode="python"):
        return {"verdict": self.verdict.value, "summary": self.summary}


def test_processor_persists_technical_evidence_before_gemini(
    tmp_path, monkeypatch
):
    engine, factory = _make_db(tmp_path, monkeypatch)
    _create_processing_audit(factory)

    work_dir = tmp_path / "audit-work"
    work_dir.mkdir()
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"test-video")

    metadata = {
        "probe": "ffprobe",
        "duration_seconds": 12.5,
        "file_size_bytes": 10,
        "has_audio": False,
        "video": {"width": 1920, "height": 1080, "frame_rate": 30.0},
        "audio_streams": [],
    }
    findings = [
        {
            "code": "audio_stream_missing",
            "severity": "warning",
            "source": "ffprobe",
            "confidence": "high",
            "description": "Test observation",
            "evidence": {},
        }
    ]

    monkeypatch.setattr(
        processor,
        "download_video",
        lambda *args, **kwargs: DownloadedVideo(path=video_path, work_dir=work_dir),
    )
    monkeypatch.setattr(
        processor,
        "probe_video",
        lambda path: VideoProbeResult(metadata=metadata, findings=findings),
    )

    def fake_gemini(*args, **kwargs):
        with factory() as db:
            record = db.scalar(
                select(AuditRecord).where(AuditRecord.id == "audit-technical")
            )
            assert record.technical_metadata == metadata
            assert record.technical_findings == findings
        return _FakeReport()

    monkeypatch.setattr(processor, "analyze_youtube_video", fake_gemini)

    try:
        processor.process_audit("audit-technical", "test-lease")

        with factory() as db:
            record = db.get(AuditRecord, "audit-technical")
            assert record.status == "completed"
            assert record.technical_metadata == metadata
            assert record.technical_findings == findings
            assert record.markup_comment == "Предварительно подходит."
            assert record.verdict == "candidate_accepted"
        assert not work_dir.exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_processor_continues_when_ffprobe_is_optional_and_missing(
    tmp_path, monkeypatch
):
    engine, factory = _make_db(tmp_path, monkeypatch)
    _create_processing_audit(factory, "audit-no-ffprobe")
    monkeypatch.setattr(processor.settings, "ffprobe_required", False)

    work_dir = tmp_path / "audit-work-no-ffprobe"
    work_dir.mkdir()
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"test-video")

    monkeypatch.setattr(
        processor,
        "download_video",
        lambda *args, **kwargs: DownloadedVideo(path=video_path, work_dir=work_dir),
    )

    def unavailable(path):
        from app.services.video_probe import FFprobeUnavailableError
        raise FFprobeUnavailableError("FFprobe missing in test")

    monkeypatch.setattr(processor, "probe_video", unavailable)

    def fake_gemini(*args, **kwargs):
        with factory() as db:
            record = db.get(AuditRecord, "audit-no-ffprobe")
            assert record.technical_metadata is None
            assert record.technical_findings[0]["code"] == "ffprobe_unavailable"
        return _FakeReport()

    monkeypatch.setattr(processor, "analyze_youtube_video", fake_gemini)

    try:
        processor.process_audit("audit-no-ffprobe", "test-lease")
        with factory() as db:
            record = db.get(AuditRecord, "audit-no-ffprobe")
            assert record.status == "completed"
            assert record.technical_metadata is None
            assert record.technical_findings[0]["code"] == "ffprobe_unavailable"
        assert not work_dir.exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
