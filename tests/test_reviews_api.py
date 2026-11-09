from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.audit import AuditRecord
from app.models.review import HumanReviewEvent
from app.models.user import UserAccount


@pytest.fixture()
def review_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
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
    with TestClient(app) as client:
        yield client, factory
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def create_user(factory, email: str, role: str, active: bool = True) -> str:
    with factory() as db:
        user = UserAccount(
            email=email,
            password_hash=hash_password("correct-horse-battery"),
            role=role,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user.id


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
        return audit.id


def token(client: TestClient, email: str) -> str:
    response = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token_value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token_value}"}


def test_review_queue_claim_decision_and_append_only_history(review_client):
    client, factory = review_client
    create_user(factory, "operator@example.com", "operator")
    reviewer_id = create_user(factory, "reviewer@example.com", "reviewer")
    other_reviewer_id = create_user(factory, "other@example.com", "reviewer")
    audit_id = create_audit(factory)
    reviewer_headers = auth(token(client, "reviewer@example.com"))
    other_headers = auth(token(client, "other@example.com"))
    operator_headers = auth(token(client, "operator@example.com"))

    assert client.get("/api/v1/reviews/queue").status_code == 401
    assert client.get("/api/v1/reviews/queue", headers=operator_headers).status_code == 403

    queue = client.get("/api/v1/reviews/queue", headers=reviewer_headers)
    assert queue.status_code == 200
    assert [item["id"] for item in queue.json()] == [audit_id]

    no_claim = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=reviewer_headers,
        json={"decision": "approved"},
    )
    assert no_claim.status_code == 409

    claimed = client.post(f"/api/v1/reviews/{audit_id}/claim", headers=reviewer_headers)
    assert claimed.status_code == 200
    assert claimed.json()["review_status"] == "assigned"
    assert claimed.json()["reviewer_user_id"] == reviewer_id

    competing_claim = client.post(
        f"/api/v1/reviews/{audit_id}/claim", headers=other_headers
    )
    assert competing_claim.status_code == 409

    wrong_reviewer = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=other_headers,
        json={"decision": "rejected", "comment": "This is not my assigned task."},
    )
    assert wrong_reviewer.status_code == 403

    missing_reason = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=reviewer_headers,
        json={"decision": "rejected", "comment": "no"},
    )
    assert missing_reason.status_code == 422

    submitted = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=reviewer_headers,
        json={"decision": "rejected", "comment": "Visible defect confirmed in the source."},
    )
    assert submitted.status_code == 200, submitted.text
    data = submitted.json()
    assert data["review_status"] == "completed"
    assert data["human_review_decision"] == "rejected"
    assert data["human_reviewed_by_user_id"] == reviewer_id
    # Human adjudication never rewrites the machine-generated result.
    assert data["verdict"] == "manual_review_required"
    assert data["decision"] == "REVIEW"

    repeat = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=reviewer_headers,
        json={"decision": "approved"},
    )
    assert repeat.status_code == 409

    history = client.get(
        f"/api/v1/reviews/{audit_id}/history", headers=reviewer_headers
    )
    assert history.status_code == 200
    assert [event["action"] for event in history.json()] == [
        "claimed",
        "decision_submitted",
    ]
    assert history.json()[-1]["decision"] == "rejected"

    with factory() as db:
        events = list(
            db.scalars(
                select(HumanReviewEvent).where(HumanReviewEvent.audit_id == audit_id)
            ).all()
        )
        assert len(events) == 2


def test_admin_assignment_reopen_and_reviewer_directory(review_client):
    client, factory = review_client
    admin_id = create_user(factory, "admin@example.com", "admin")
    reviewer_id = create_user(factory, "reviewer@example.com", "reviewer")
    operator_id = create_user(factory, "operator@example.com", "operator")
    audit_id = create_audit(factory)
    admin_headers = auth(token(client, "admin@example.com"))
    reviewer_headers = auth(token(client, "reviewer@example.com"))

    directory = client.get("/api/v1/auth/reviewers", headers=admin_headers)
    assert directory.status_code == 200
    assert [item["id"] for item in directory.json()] == [reviewer_id]

    forbidden_directory = client.get(
        "/api/v1/auth/reviewers", headers=reviewer_headers
    )
    assert forbidden_directory.status_code == 403

    invalid_assignment = client.put(
        f"/api/v1/reviews/{audit_id}/assignment",
        headers=admin_headers,
        json={"reviewer_user_id": operator_id},
    )
    assert invalid_assignment.status_code == 422

    assigned = client.put(
        f"/api/v1/reviews/{audit_id}/assignment",
        headers=admin_headers,
        json={"reviewer_user_id": reviewer_id, "comment": "Assigned for specialist review."},
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["review_status"] == "assigned"
    assert assigned.json()["reviewer_user_id"] == reviewer_id

    accepted = client.post(
        f"/api/v1/reviews/{audit_id}/decision",
        headers=admin_headers,
        json={"decision": "approved", "comment": "Reviewed against the project checklist."},
    )
    assert accepted.status_code == 200
    assert accepted.json()["human_review_decision"] == "approved"

    reopened = client.post(
        f"/api/v1/reviews/{audit_id}/reopen",
        headers=admin_headers,
        json={"comment": "A second expert opinion is required."},
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["review_status"] == "pending"
    assert reopened.json()["reviewer_user_id"] is None
    assert reopened.json()["human_review_decision"] is None

    history = client.get(
        f"/api/v1/reviews/{audit_id}/history", headers=admin_headers
    )
    assert [event["action"] for event in history.json()] == [
        "assigned",
        "decision_submitted",
        "reopened",
    ]
    assert history.json()[-1]["decision"] == "approved"

    completed_queue = client.get(
        "/api/v1/reviews/queue?review_status=completed", headers=admin_headers
    )
    assert completed_queue.status_code == 200
    assert completed_queue.json() == []

    with factory() as db:
        audit = db.get(AuditRecord, audit_id)
        assert audit.verdict == "manual_review_required"
        assert audit.decision == "REVIEW"
        assert audit.review_status == "pending"
