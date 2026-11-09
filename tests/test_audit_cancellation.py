from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.audit import AuditRecord
from app.models.user import UserAccount
from app.services.cancellation import AuditCancelled, check_audit_cancellation


@pytest.fixture()
def cancellation_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    monkeypatch.setattr(
        "app.core.security.settings.auth_secret_key",
        "test-secret-key-with-at-least-32-characters",
    )
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


def _add_user(factory, email: str, role: str) -> str:
    with factory() as db:
        user = UserAccount(
            email=email,
            password_hash=hash_password("correct-horse-battery"),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id


def _add_audit(factory, owner_id: str, status: str) -> str:
    with factory() as db:
        audit = AuditRecord(
            youtube_url="https://www.youtube.com/watch?v=abcdefghijk",
            video_id="abcdefghijk",
            content_type="unknown",
            status=status,
            created_by_user_id=owner_id,
            attempt_count=1 if status in {"processing", "cancelling"} else 0,
            worker_id="test-worker" if status in {"processing", "cancelling"} else None,
            lease_token="test-lease" if status in {"processing", "cancelling"} else None,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return audit.id


def _token(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_admin_can_cancel_pending_audit_immediately(cancellation_client):
    client, factory = cancellation_client
    admin_id = _add_user(factory, "admin@example.com", "admin")
    audit_id = _add_audit(factory, admin_id, "pending")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=_token(client, "admin@example.com"),
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelled"
    assert response.json()["processing_stage"] is None
    with factory() as db:
        record = db.get(AuditRecord, audit_id)
        assert record.completed_at is not None
        assert record.next_retry_at is None


def test_operator_requests_cancellation_and_worker_finalizes_it(cancellation_client):
    client, factory = cancellation_client
    operator_id = _add_user(factory, "operator@example.com", "operator")
    audit_id = _add_audit(factory, operator_id, "processing")
    headers = _token(client, "operator@example.com")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=headers,
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


def test_operator_cannot_cancel_another_operators_audit(cancellation_client):
    client, factory = cancellation_client
    owner_id = _add_user(factory, "owner@example.com", "operator")
    other_id = _add_user(factory, "other@example.com", "operator")
    audit_id = _add_audit(factory, owner_id, "pending")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=_token(client, "other@example.com"),
    )

    assert response.status_code == 404
    with factory() as db:
        assert db.get(AuditRecord, audit_id).status == "pending"


def test_reviewer_cannot_cancel_audit(cancellation_client):
    client, factory = cancellation_client
    owner_id = _add_user(factory, "owner@example.com", "operator")
    _add_user(factory, "reviewer@example.com", "reviewer")
    audit_id = _add_audit(factory, owner_id, "pending")

    response = client.post(
        f"/api/v1/audits/{audit_id}/cancel",
        headers=_token(client, "reviewer@example.com"),
    )

    assert response.status_code == 403


def test_cancel_is_idempotent_and_completed_audit_cannot_be_cancelled(cancellation_client):
    client, factory = cancellation_client
    admin_id = _add_user(factory, "admin@example.com", "admin")
    pending_id = _add_audit(factory, admin_id, "pending")
    completed_id = _add_audit(factory, admin_id, "completed")
    headers = _token(client, "admin@example.com")

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
    from app.models.audit import AuditRecord
    from app.services import processor

    _client, factory = cancellation_client
    operator_id = _add_user(factory, "operator@example.com", "operator")
    audit_id = _add_audit(factory, operator_id, "processing")
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
