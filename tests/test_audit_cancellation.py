from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.audit import AuditRecord
from app.models.user import UserAccount  # Register legacy foreign-key table for SQLite metadata.
from app.services.cancellation import AuditCancelled, check_audit_cancellation


@pytest.fixture()
def cancellation_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    monkeypatch.setattr(settings, "workspace_access_key", "shared-team-secret")
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr("app.services.cancellation.SessionLocal", factory)
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def _headers(key: str = "shared-team-secret") -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _add_audit(factory, status: str) -> str:
    with factory() as db:
        audit = AuditRecord(
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            content_type="unknown",
            status=status,
            attempt_count=1 if status in {"processing", "cancelling"} else 0,
            worker_id="test-worker" if status in {"processing", "cancelling"} else None,
            lease_token="test-lease" if status in {"processing", "cancelling"} else None,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return audit.id


def test_shared_workspace_key_is_required_for_audit_endpoints(cancellation_client):
    client, _factory = cancellation_client

    assert client.get("/health").status_code == 200
    assert client.get("/api/v1/audits").status_code == 401
    assert client.get("/api/v1/audits", headers=_headers("wrong-key")).status_code == 401
    assert client.get("/api/v1/audits", headers=_headers()).status_code == 200


def test_team_member_can_cancel_pending_audit_immediately(cancellation_client):
    client, factory = cancellation_client
    audit_id = _add_audit(factory, "pending")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["processing_stage"] is None
    with factory() as db:
        record = db.get(AuditRecord, audit_id)
        assert record.completed_at is not None
        assert record.next_retry_at is None


def test_team_member_requests_cancellation_and_worker_finalizes_it(cancellation_client):
    client, factory = cancellation_client
    audit_id = _add_audit(factory, "processing")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=_headers(),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelling"

    with pytest.raises(AuditCancelled):
        check_audit_cancellation(audit_id, "test-lease")

    with factory() as db:
        record = db.get(AuditRecord, audit_id)
        assert record.status == "cancelled"
        assert record.lease_token is None
        assert record.worker_id is None
        assert record.completed_at is not None
        assert record.processing_stage is None


def test_all_team_members_see_the_same_audit_history(cancellation_client):
    client, factory = cancellation_client
    audit_id = _add_audit(factory, "completed")

    response = client.get("/api/v1/audits", headers=_headers())
    detail = client.get(f"/api/v1/audits/{audit_id}", headers=_headers("shared-team-secret"))

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [audit_id]
    assert detail.status_code == 200


def test_cancel_is_idempotent_and_completed_audit_cannot_be_cancelled(cancellation_client):
    client, factory = cancellation_client
    pending_id = _add_audit(factory, "pending")
    completed_id = _add_audit(factory, "completed")
    headers = _headers()

    first = client.post(f"/api/v1/audits/{pending_id}/cancel", headers=headers)
    second = client.post(f"/api/v1/audits/{pending_id}/cancel", headers=headers)
    completed = client.post(f"/api/v1/audits/{completed_id}/cancel", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "cancelled"
    assert completed.status_code == 409


def test_processor_stops_without_retry_when_cancelled_during_download(
    cancellation_client, monkeypatch
):
    from app.services import processor

    _client, factory = cancellation_client
    audit_id = _add_audit(factory, "processing")
    monkeypatch.setattr(processor, "SessionLocal", factory)

    def request_cancel(_url, _audit_id, *, cancel_check):
        with factory() as db:
            record = db.get(AuditRecord, audit_id)
            record.status = "cancelling"
            db.commit()
        cancel_check()
        raise AssertionError("Cancellation checkpoint should stop the worker")

    monkeypatch.setattr(processor, "download_video", request_cancel)

    processor.process_audit(audit_id, "test-lease")

    with factory() as db:
        record = db.get(AuditRecord, audit_id)
        assert record.status == "cancelled"
        assert record.lease_token is None
        assert record.worker_id is None
        assert record.processing_stage is None
        assert record.report is None
        assert record.attempt_count == 1
        assert record.next_retry_at is None
