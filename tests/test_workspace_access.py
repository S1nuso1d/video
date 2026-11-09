from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.user import UserAccount  # Register legacy foreign-key table for SQLite metadata.


def _client(monkeypatch, key: str):
    monkeypatch.setattr(settings, "workspace_access_key", key)
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
    return TestClient(app), engine


def test_workspace_key_is_optional_for_local_development(monkeypatch):
    client, engine = _client(monkeypatch, "")
    try:
        assert client.get("/api/v1/audits").status_code == 200
        assert client.get("/api/v1/reviews/queue").status_code == 200
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_workspace_key_is_shared_and_account_apis_are_removed(monkeypatch):
    client, engine = _client(monkeypatch, "same-key-for-everyone")
    try:
        assert client.get("/api/v1/audits").status_code == 401
        assert client.get(
            "/api/v1/audits",
            headers={"Authorization": "Bearer invalid"},
        ).status_code == 401
        headers = {"Authorization": "Bearer same-key-for-everyone"}
        assert client.get("/api/v1/audits", headers=headers).status_code == 200
        assert client.get("/api/v1/reviews/queue", headers=headers).status_code == 200
        created = client.post(
            "/api/v1/audits",
            headers=headers,
            json={
                "youtube_url": "https://www.youtube.com/watch?v=aqz-KE-bpKQ",
                "content_type": "live_action",
            },
        )
        assert created.status_code == 202, created.text
        assert created.json()["status"] == "pending"
        assert "created_by_user_id" not in created.json()
        assert client.post(
            "/api/v1/auth/token",
            json={"email": "nobody@example.com", "password": "pass"},
        ).status_code == 404
        assert client.get("/api/v1/admin/overview", headers=headers).status_code == 404
    finally:
        client.close()
        app.dependency_overrides.clear()
        Base.metadata.drop_all(engine)
        engine.dispose()
