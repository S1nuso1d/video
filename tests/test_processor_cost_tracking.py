from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.audit import AuditRecord
from app.models.review import HumanReviewEvent
from app.services import processor
from app.services.gemini_auditor import GeminiAuditError, GeminiAuditResult
from app.services.video_downloader import DownloadedVideo
from app.services.video_probe import VideoProbeResult
from app.services.frame_analyzer import FrameAnalysisResult


class FakeReport:
    verdict = SimpleNamespace(value="candidate_accepted")
    summary = "Test audit completed."
    detected_defects = []
    markup_comment = "Предварительно подходит."

    def model_dump(self, mode="python"):
        return {
            "verdict": self.verdict.value,
            "summary": self.summary,
            "review_coverage": "adequate_for_preliminary_assessment",
            "detected_defects": [],
        }


def _prepare(tmp_path, monkeypatch, audit_id):
    engine = create_engine(f"sqlite:///{tmp_path / (audit_id + '.db')}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(processor, "SessionLocal", factory)

    with factory() as db:
        db.add(AuditRecord(
            id=audit_id,
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            content_type="live_action",
            status="processing",
            attempt_count=1,
            worker_id="test-worker",
            lease_token="test-lease",
        ))
        db.commit()

    work_dir = tmp_path / (audit_id + "-work")
    work_dir.mkdir()
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"test-video")

    monkeypatch.setattr(
        processor,
        "download_video",
        lambda *args, **kwargs: DownloadedVideo(
            path=video_path,
            work_dir=work_dir,
        ),
    )
    monkeypatch.setattr(
        processor,
        "probe_video",
        lambda path: VideoProbeResult(
            metadata={
                "duration_seconds": 12.0,
                "file_size_bytes": 10,
                "has_audio": True,
                "video": {"width": 1920, "height": 1080, "frame_rate": 30.0},
                "audio_streams": [{"codec": "aac"}],
            },
            findings=[],
        ),
    )
    monkeypatch.setattr(
        processor,
        "analyze_video_frames",
        lambda path, duration_seconds=None: FrameAnalysisResult(
            metadata={"sampled_frames": 2, "sample_interval_seconds": 0.5},
            findings=[],
        ),
    )
    monkeypatch.setattr(processor.settings, "gemini_input_price_usd_per_million", 0.75)
    monkeypatch.setattr(processor.settings, "gemini_cached_input_price_usd_per_million", 0.075)
    monkeypatch.setattr(processor.settings, "gemini_output_price_usd_per_million", 3.75)
    monkeypatch.setattr(processor.settings, "gemini_pricing_version", "test-rate-card")
    monkeypatch.setattr(processor.settings, "gemini_usd_to_rub_rate", 80.0)
    return engine, factory, work_dir


def test_processor_persists_usage_cost_and_stage_timings_on_success(
    tmp_path, monkeypatch
):
    engine, factory, work_dir = _prepare(tmp_path, monkeypatch, "audit-cost-success")
    monkeypatch.setattr(
        processor,
        "analyze_youtube_video",
        lambda *args, **kwargs: GeminiAuditResult(
            report=FakeReport(),
            usage={
                "input_tokens": 1_000_000,
                "output_tokens": 100_000,
                "thought_tokens": 20_000,
                "cached_input_tokens": 200_000,
                "total_tokens": 1_100_000,
            },
        ),
    )

    try:
        processor.process_audit("audit-cost-success", "test-lease")
        with factory() as db:
            record = db.get(AuditRecord, "audit-cost-success")
            assert record.status == "completed"
            assert record.gemini_usage["request_count"] == 1
            assert record.gemini_cost_usd == 0.99
            assert record.gemini_cost_rub == 79.2
            assert record.processing_metrics["attempt_count"] == 1
            assert set(record.processing_metrics["last_attempt"]["stage_durations_seconds"]) == {
                "download", "ffprobe", "opencv", "gemini"
            }
        assert not work_dir.exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_processor_routes_manual_review_report_to_queue(tmp_path, monkeypatch):
    engine, factory, work_dir = _prepare(tmp_path, monkeypatch, "audit-review-queue")

    class ManualReviewReport(FakeReport):
        verdict = SimpleNamespace(value="manual_review_required")
        summary = "The evidence is ambiguous."
        markup_comment = "Нужна ручная проверка."

    monkeypatch.setattr(
        processor,
        "analyze_youtube_video",
        lambda *args, **kwargs: GeminiAuditResult(
            report=ManualReviewReport(),
            usage=None,
        ),
    )

    try:
        processor.process_audit("audit-review-queue", "test-lease")
        with factory() as db:
            record = db.get(AuditRecord, "audit-review-queue")
            assert record.status == "completed"
            assert record.verdict == "manual_review_required"
            assert record.review_status == "pending"
            assert record.human_review_decision is None
            events = list(
                db.query(HumanReviewEvent)
                .filter(HumanReviewEvent.audit_id == "audit-review-queue")
                .all()
            )
            assert len(events) == 1
            assert events[0].action == "queued"
            assert events[0].to_status == "pending"
            assert "AI verdict" in events[0].comment
        assert not work_dir.exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_processor_preserves_usage_when_report_validation_fails_after_response(
    tmp_path, monkeypatch
):
    engine, factory, work_dir = _prepare(tmp_path, monkeypatch, "audit-cost-failed")
    def fail_after_response(*args, **kwargs):
        raise GeminiAuditError(
            "Invalid model response",
            usage={
                "input_tokens": 500,
                "output_tokens": 100,
                "thought_tokens": 25,
                "cached_input_tokens": 0,
                "total_tokens": 600,
            },
            request_made=True,
        )

    monkeypatch.setattr(processor, "analyze_youtube_video", fail_after_response)

    try:
        processor.process_audit("audit-cost-failed", "test-lease")
        with factory() as db:
            record = db.get(AuditRecord, "audit-cost-failed")
            assert record.status == "failed"
            assert record.gemini_usage["request_count"] == 1
            assert record.gemini_usage["input_tokens"] == 500
            assert record.gemini_cost_usd is not None
            assert record.processing_metrics["attempt_count"] == 1
        assert not work_dir.exists()
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
