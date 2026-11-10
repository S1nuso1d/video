from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.audit import AuditRecord
from app.models.review import HumanReviewEvent
from app.models.user import UserAccount  # Register legacy foreign-key table for SQLite metadata.


@pytest.fixture()
def review_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
    monkeypatch.setattr(settings, "workspace_access_key", "shared-workspace-key-for-tests-with-at-least-32-characters")
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
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def headers() -> dict[str, str]:
    return {"Authorization": "Bearer shared-workspace-key-for-tests-with-at-least-32-characters"}


def create_audit(factory, *, review_status: str = "pending", video_id: str = "dQw4w9WgXcQ") -> str:
    with factory() as db:
        audit = AuditRecord(
            youtube_url=f"https://www.youtube.com/watch?v={video_id}",
            video_id=video_id,
            content_type="live_action",
            status="completed",
            verdict="manual_review_required",
            decision="REVIEW",
            review_status=review_status,
            report={
                "verdict": "manual_review_required",
                "summary": "Ambiguous evidence requires an expert decision.",
                "review_coverage": "limited",
                "detected_defects": [],
                "not_assessed": ["Audio context"],
                "manual_review_reasons": ["The sampled evidence is inconclusive."],
                "markup_comment": "Please inspect this video manually.",
            },
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return audit.id


def test_team_can_review_queue_and_submit_shared_decision(review_client):
    client, factory = review_client
    audit_id = create_audit(factory)

    assert client.get("/api/v1/reviews/queue").status_code == 401
    queue = client.get("/api/v1/reviews/queue", headers=headers())
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()] == [audit_id]

    missing_reason = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=headers(),
        json={"decision": "rejected", "comment": "no"},
    )
    assert missing_reason.status_code == 422

    submitted = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=headers(),
        json={"decision": "rejected", "comment": "Visible defect confirmed in the source."},
    )
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()
    assert data["review_status"] == "completed"
    assert data["human_review_decision"] == "rejected"
    assert "reviewer_user_id" not in data
    assert "human_reviewed_by_user_id" not in data
    # Human adjudication never rewrites the machine-generated result.
    assert data["verdict"] == "manual_review_required"
    assert data["decision"] == "REVIEW"

    repeat = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=headers(),
        json={"decision": "approved"},
    )
    assert repeat.status_code == 409

    history = client.get(
        f"/api/v1/reviews/{audit_id}/history", headers=headers()
    )
    assert history.status_code == 200
    assert [event["action"] for event in history.json()] == ["decision_submitted"]
    assert "actor_user_id" not in history.json()[0]

    with factory() as db:
        events = list(
            db.scalars(
                select(HumanReviewEvent).where(HumanReviewEvent.audit_id == audit_id)
            ).all()
        )
        assert len(events) == 1
        assert events[0].actor_user_id is None


def test_team_can_reopen_completed_review_without_assigning_a_reviewer(review_client):
    client, factory = review_client
    audit_id = create_audit(factory)
    accepted = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=headers(),
        json={"decision": "approved"},
    )
    assert accepted.status_code == 200

    reopened = client.post(
        f"/api/v1/reviews/{audit_id}/reopen",
        headers=headers(),
        json={"comment": "A second opinion is required."},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["review_status"] == "pending"
    assert reopened.json()["human_review_decision"] is None
    assert "reviewer_user_id" not in reopened.json()

    history = client.get(
        f"/api/v1/reviews/{audit_id}/history", headers=headers()
    )
    assert [event["action"] for event in history.json()] == [
        "decision_submitted",
        "reopened",
    ]
    assert history.json()[-1]["decision"] == "approved"


def test_review_queue_includes_historical_assigned_items(review_client):
    client, factory = review_client
    assigned_id = create_audit(factory, review_status="assigned")
    queue = client.get("/api/v1/reviews/queue", headers=headers())
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()] == [assigned_id]
