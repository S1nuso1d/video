from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.audit import AuditRecord
from app.worker import claim_next_job, recover_stale_jobs, refresh_job_lease


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-leases.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("app.worker.SessionLocal", factory)
    monkeypatch.setattr("app.worker.settings.worker_lease_timeout_seconds", 60)
    monkeypatch.setattr("app.worker.settings.worker_heartbeat_seconds", 5)
    monkeypatch.setattr("app.worker.settings.worker_recovery_interval_seconds", 5)
    monkeypatch.setattr("app.worker.settings.audit_max_attempts", 2)
    monkeypatch.setattr("app.worker.settings.audit_retry_delay_seconds", 30)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


def _insert_audit(
    factory,
    *,
    audit_id="audit-1",
    status="pending",
    attempt_count=0,
    started_at=None,
    heartbeat_at=None,
    worker_id=None,
    lease_token=None,
):
    with factory() as db:
        record = AuditRecord(
            id=audit_id,
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            content_type="unknown",
            status=status,
            attempt_count=attempt_count,
            started_at=started_at,
            heartbeat_at=heartbeat_at,
            worker_id=worker_id,
            lease_token=lease_token,
        )
        db.add(record)
        db.commit()
    return audit_id


def _get_audit(factory, audit_id):
    with factory() as db:
        return db.scalar(select(AuditRecord).where(AuditRecord.id == audit_id))


def test_claim_next_job_assigns_lease_and_counts_attempt(db_factory):
    audit_id = _insert_audit(db_factory)

    claim = claim_next_job("test-worker")

    assert claim is not None
    assert claim.audit_id == audit_id
    assert claim.attempt_number == 1
    assert claim.lease_token

    record = _get_audit(db_factory, audit_id)
    assert record.status == "processing"
    assert record.attempt_count == 1
    assert record.worker_id == "test-worker"
    assert record.lease_token == claim.lease_token
    assert record.heartbeat_at is not None


def test_recovery_requeues_stale_job_when_attempts_remain(db_factory):
    now = datetime.now(timezone.utc)
    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=1,
        started_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(minutes=5),
        worker_id="dead-worker",
        lease_token="old-lease",
    )

    assert recover_stale_jobs(now=now) == 1

    record = _get_audit(db_factory, audit_id)
    assert record.status == "pending"
    assert record.attempt_count == 1
    assert record.next_retry_at is not None
    assert record.started_at is None
    assert record.heartbeat_at is None
    assert record.worker_id is None
    assert record.lease_token is None
    assert "returned to the queue" in record.error_message


def test_recovery_fails_stale_job_when_attempt_budget_is_exhausted(db_factory):
    now = datetime.now(timezone.utc)
    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=2,
        started_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(minutes=5),
        worker_id="dead-worker",
        lease_token="old-lease",
    )

    assert recover_stale_jobs(now=now) == 1

    record = _get_audit(db_factory, audit_id)
    assert record.status == "failed"
    assert record.completed_at is not None
    assert record.next_retry_at is None
    assert record.worker_id is None
    assert record.lease_token is None
    assert "maximum number of attempts" in record.error_message


def test_recovery_does_not_touch_job_with_recent_heartbeat(db_factory):
    now = datetime.now(timezone.utc)
    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=1,
        started_at=now - timedelta(minutes=5),
        heartbeat_at=now - timedelta(seconds=20),
        worker_id="live-worker",
        lease_token="live-lease",
    )

    assert recover_stale_jobs(now=now) == 0

    record = _get_audit(db_factory, audit_id)
    assert record.status == "processing"
    assert record.lease_token == "live-lease"


def test_only_current_lease_owner_can_refresh_heartbeat(db_factory):
    now = datetime.now(timezone.utc)
    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=1,
        started_at=now,
        heartbeat_at=now - timedelta(seconds=20),
        worker_id="live-worker",
        lease_token="current-lease",
    )

    assert refresh_job_lease(audit_id, "stale-lease") is False
    assert refresh_job_lease(audit_id, "current-lease") is True
    record = _get_audit(db_factory, audit_id)
    assert record.heartbeat_at is not None
    assert record.lease_token == "current-lease"


def test_claim_skips_job_whose_retry_time_is_in_the_future(db_factory):
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    with db_factory() as db:
        db.add(
            AuditRecord(
                id="future-audit",
                youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
                video_id="abcdefghijk",
                content_type="unknown",
                status="pending",
                next_retry_at=future,
            )
        )
        db.commit()

    assert claim_next_job("test-worker") is None
    record = _get_audit(db_factory, "future-audit")
    assert record.status == "pending"


def test_stale_worker_cannot_start_processing_without_current_lease(db_factory, monkeypatch):
    from app.services import processor

    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=1,
        started_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        worker_id="new-worker",
        lease_token="new-lease",
    )
    monkeypatch.setattr(processor, "SessionLocal", db_factory)

    def unexpected_download(*args, **kwargs):
        raise AssertionError("A worker with an expired lease must not download video")

    monkeypatch.setattr(processor, "download_video", unexpected_download)

    processor.process_audit(audit_id, "old-lease")

    record = _get_audit(db_factory, audit_id)
    assert record.status == "processing"
    assert record.lease_token == "new-lease"



def test_expired_worker_cannot_overwrite_result_after_lease_changes(db_factory, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from app.services import processor
    from app.services.video_downloader import DownloadedVideo

    audit_id = _insert_audit(
        db_factory,
        status="processing",
        attempt_count=1,
        started_at=datetime.now(timezone.utc),
        heartbeat_at=datetime.now(timezone.utc),
        worker_id="old-worker",
        lease_token="old-lease",
    )
    monkeypatch.setattr(processor, "SessionLocal", db_factory)
    work_dir = tmp_path / "audit-work"
    work_dir.mkdir()
    video_path = work_dir / "video.mp4"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(
        processor,
        "download_video",
        lambda *args, **kwargs: DownloadedVideo(path=video_path, work_dir=work_dir),
    )

    class FakeReport:
        verdict = SimpleNamespace(value="candidate_accepted")
        summary = "Test report"
        detected_defects = []
        markup_comment = "Test comment"

        def model_dump(self, mode="python"):
            return {"verdict": "candidate_accepted", "summary": self.summary}

    def change_lease_during_analysis(*args, **kwargs):
        with db_factory() as db:
            record = db.get(AuditRecord, audit_id)
            record.worker_id = "new-worker"
            record.lease_token = "new-lease"
            record.heartbeat_at = datetime.now(timezone.utc)
            db.commit()
        return FakeReport()

    monkeypatch.setattr(processor, "analyze_youtube_video", change_lease_during_analysis)

    processor.process_audit(audit_id, "old-lease")

    record = _get_audit(db_factory, audit_id)
    assert record.status == "processing"
    assert record.worker_id == "new-worker"
    assert record.lease_token == "new-lease"
    assert record.report is None
    assert not work_dir.exists()
