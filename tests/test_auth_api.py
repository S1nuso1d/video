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


@pytest.fixture()
def api_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    monkeypatch.setattr(
        "app.core.security.settings.auth_secret_key",
        "test-secret-key-with-at-least-32-characters",
    )
    monkeypatch.setattr("app.core.security.settings.auth_access_token_minutes", 30)
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestingSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        yield client, TestingSession
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def add_user(session_factory, email: str, role: str, password: str = "correct-horse-battery"):
    with session_factory() as db:
        user = UserAccount(
            email=email,
            password_hash=hash_password(password),
            role=role,
            is_active=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user.id


def add_audit(session_factory, owner_id: str, video_id: str):
    with session_factory() as db:
        audit = AuditRecord(
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
            content_type="unknown",
            status="completed",
            created_by_user_id=owner_id,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return audit.id


def login(client: TestClient, email: str, password: str = "correct-horse-battery") -> str:
    response = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_operator_is_limited_to_own_audits_and_reviewer_can_see_queue(api_client):
    client, session_factory = api_client
    operator_id = add_user(session_factory, "operator@example.com", "operator")
    other_operator_id = add_user(session_factory, "other@example.com", "operator")
    reviewer_id = add_user(session_factory, "reviewer@example.com", "reviewer")
    own_audit = add_audit(session_factory, operator_id, "dQw4w9WgXcQ")
    foreign_audit = add_audit(session_factory, other_operator_id, "M7lc1UVf-VE")

    operator_token = login(client, "OPERATOR@example.com")
    operator_headers = {"Authorization": f"Bearer {operator_token}"}
    listed = client.get("/api/v1/audits", headers=operator_headers)
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [own_audit]
    assert client.get(
        f"/api/v1/audits/{foreign_audit}", headers=operator_headers
    ).status_code == 404

    created = client.post(
        "/api/v1/audits",
        headers=operator_headers,
        json={
            "youtube_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "content_type": "live_action",
        },
    )
    assert created.status_code == 202, created.text
    with session_factory() as db:
        stored = db.get(AuditRecord, created.json()["id"])
        assert stored.created_by_user_id == operator_id

    reviewer_token = login(client, "reviewer@example.com")
    reviewer_headers = {"Authorization": f"Bearer {reviewer_token}"}
    reviewer_list = client.get("/api/v1/audits", headers=reviewer_headers)
    assert reviewer_list.status_code == 200
    assert {item["id"] for item in reviewer_list.json()} == {
        own_audit,
        foreign_audit,
        created.json()["id"],
    }
    assert client.post(
        "/api/v1/audits",
        headers=reviewer_headers,
        json={
            "youtube_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
            "content_type": "unknown",
        },
    ).status_code == 403


def test_authentication_and_admin_user_provisioning(api_client):
    client, session_factory = api_client
    admin_id = add_user(session_factory, "admin@example.com", "admin")
    operator_id = add_user(session_factory, "operator@example.com", "operator")

    assert client.get("/api/v1/audits").status_code == 401
    assert client.post(
        "/api/v1/auth/token",
        json={"email": "admin@example.com", "password": "wrong-password"},
    ).status_code == 401

    admin_headers = {
        "Authorization": f"Bearer {login(client, 'admin@example.com')}"
    }
    me = client.get("/api/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200
    assert me.json()["role"] == "admin"
    assert "password_hash" not in me.json()

    created = client.post(
        "/api/v1/auth/users",
        headers=admin_headers,
        json={
            "email": "new.reviewer@example.com",
            "password": "long-enough-new-password",
            "role": "reviewer",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["role"] == "reviewer"
    assert "password_hash" not in created.json()

    duplicate = client.post(
        "/api/v1/auth/users",
        headers=admin_headers,
        json={
            "email": "NEW.REVIEWER@example.com",
            "password": "long-enough-new-password",
            "role": "reviewer",
        },
    )
    assert duplicate.status_code == 409

    operator_headers = {
        "Authorization": f"Bearer {login(client, 'operator@example.com')}"
    }
    forbidden = client.post(
        "/api/v1/auth/users",
        headers=operator_headers,
        json={
            "email": "blocked@example.com",
            "password": "long-enough-new-password",
            "role": "operator",
        },
    )
    assert forbidden.status_code == 403
