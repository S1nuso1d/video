from collections.abc import Generator
from datetime import datetime, timedelta, timezone

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
def admin_client(monkeypatch) -> Generator[tuple[TestClient, sessionmaker], None, None]:
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


def login(client: TestClient, email: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/token",
        json={"email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def add_audit(
    factory,
    *,
    audit_id: str,
    created_at: datetime,
    status: str = "completed",
    verdict: str | None = None,
    criteria_decision: str | None = None,
    review_status: str = "not_required",
    reviewer_user_id: str | None = None,
    reviewed_by_user_id: str | None = None,
    human_decision: str | None = None,
    completed_at: datetime | None = None,
    reviewed_at: datetime | None = None,
    cost_usd: float | None = None,
    cost_rub: float | None = None,
    usage: dict | None = None,
    attempts: int = 1,
):
    with factory() as db:
        record = AuditRecord(
            id=audit_id,
            youtube_url=f"https://www.youtube.com/watch?v={audit_id[:11]}",
            video_id=audit_id[:11],
            content_type="live_action",
            status=status,
            verdict=verdict,
            decision=criteria_decision,
            review_status=review_status,
            reviewer_user_id=reviewer_user_id,
            human_reviewed_by_user_id=reviewed_by_user_id,
            human_review_decision=human_decision,
            created_at=created_at,
            updated_at=created_at,
            completed_at=completed_at,
            human_reviewed_at=reviewed_at,
            gemini_cost_usd=cost_usd,
            gemini_cost_rub=cost_rub,
            gemini_usage=usage,
            attempt_count=attempts,
        )
        db.add(record)
        db.commit()


def test_admin_overview_aggregates_audits_costs_tokens_and_reviewer_metrics(admin_client):
    client, factory = admin_client
    admin_id = create_user(factory, "admin@example.com", "admin")
    reviewer_id = create_user(factory, "reviewer@example.com", "reviewer")
    headers = login(client, "admin@example.com")
    base = datetime(2026, 11, 4, 10, 0, tzinfo=timezone.utc)

    add_audit(
        factory,
        audit_id="audit000001",
        created_at=base,
        status="completed",
        verdict="candidate_accepted",
        criteria_decision="APPROVE",
        cost_usd=0.12,
        cost_rub=9.6,
        usage={
            "input_tokens": 1000,
            "output_tokens": 200,
            "thought_tokens": 50,
            "cached_input_tokens": 100,
            "total_tokens": 1200,
            "request_count": 1,
        },
        attempts=1,
    )
    add_audit(
        factory,
        audit_id="audit000002",
        created_at=base + timedelta(hours=1),
        status="failed",
        attempts=2,
    )
    add_audit(
        factory,
        audit_id="audit000003",
        created_at=base + timedelta(hours=2),
        status="completed",
        verdict="manual_review_required",
        criteria_decision="REVIEW",
        review_status="completed",
        reviewer_user_id=reviewer_id,
        reviewed_by_user_id=reviewer_id,
        human_decision="approved",
        completed_at=base + timedelta(hours=2, minutes=2),
        reviewed_at=base + timedelta(hours=2, minutes=6),
        cost_usd=0.08,
        usage={
            "input_tokens": 500,
            "output_tokens": 100,
            "thought_tokens": 25,
            "cached_input_tokens": 0,
            "total_tokens": 600,
            "request_count": 2,
        },
        attempts=1,
    )
    add_audit(
        factory,
        audit_id="audit000004",
        created_at=base + timedelta(days=1),
        status="completed",
        verdict="manual_review_required",
        criteria_decision="REVIEW",
        review_status="assigned",
        reviewer_user_id=reviewer_id,
        attempts=1,
    )

    response = client.get(
        "/api/v1/admin/overview?date_from=2026-11-04&date_to=2026-11-05",
        headers=headers,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_audits"] == 4
    assert data["audits_by_status"] == {"completed": 3, "failed": 1}
    assert data["machine_verdicts"]["candidate_accepted"] == 1
    assert data["machine_verdicts"]["manual_review_required"] == 2
    assert data["criteria_decisions"]["REVIEW"] == 2
    assert data["human_review_statuses"]["completed"] == 1
    assert data["human_review_statuses"]["assigned"] == 1
    assert data["human_review_decisions"]["approved"] == 1
    assert data["completion_rate_percent"] == 75.0
    assert data["failure_rate_percent"] == 25.0
    assert data["gemini_estimated_cost_usd"] == pytest.approx(0.20)
    assert data["gemini_estimated_cost_rub"] == pytest.approx(9.6)
    assert data["gemini_requests"] == 3
    assert data["token_usage"]["input_tokens"] == 1500
    assert data["token_usage"]["output_tokens"] == 300
    assert data["total_attempts"] == 5
    assert len(data["daily"]) == 2

    reviewer = next(item for item in data["reviewers"] if item["user_id"] == reviewer_id)
    assert reviewer["assigned_open"] == 1
    assert reviewer["completed_reviews"] == 1
    assert reviewer["approved"] == 1
    assert reviewer["average_queue_seconds"] == 240.0


def test_admin_endpoints_require_admin_role_and_validate_date_window(admin_client):
    client, factory = admin_client
    create_user(factory, "admin@example.com", "admin")
    create_user(factory, "operator@example.com", "operator")
    operator_headers = login(client, "operator@example.com")

    assert client.get("/api/v1/admin/overview").status_code == 401
    assert client.get(
        "/api/v1/admin/overview", headers=operator_headers
    ).status_code == 403
    assert client.get(
        "/api/v1/admin/users", headers=operator_headers
    ).status_code == 403
    assert client.get(
        "/api/v1/admin/overview?date_from=2026-11-10&date_to=2026-11-01",
        headers=login(client, "admin@example.com"),
    ).status_code == 422
    assert client.get(
        "/api/v1/admin/overview?date_from=2025-01-01&date_to=2026-11-01",
        headers=login(client, "admin@example.com"),
    ).status_code == 422


def test_admin_can_manage_accounts_but_cannot_disable_last_admin(admin_client):
    client, factory = admin_client
    admin_id = create_user(factory, "admin@example.com", "admin")
    second_admin_id = create_user(factory, "second-admin@example.com", "admin")
    operator_id = create_user(factory, "operator@example.com", "operator")
    headers = login(client, "admin@example.com")

    listing = client.get("/api/v1/admin/users", headers=headers)
    assert listing.status_code == 200
    assert len(listing.json()) == 3
    assert all("password_hash" not in item for item in listing.json())

    self_disable = client.patch(
        f"/api/v1/admin/users/{admin_id}",
        headers=headers,
        json={"is_active": False},
    )
    assert self_disable.status_code == 409

    disabled_last = client.patch(
        f"/api/v1/admin/users/{second_admin_id}",
        headers=headers,
        json={"is_active": False, "role": "operator"},
    )
    assert disabled_last.status_code == 200

    promoted = client.patch(
        f"/api/v1/admin/users/{operator_id}",
        headers=headers,
        json={"role": "reviewer"},
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "reviewer"

    with factory() as db:
        remaining = db.query(UserAccount).filter_by(role="admin", is_active=True).count()
        assert remaining == 1


def test_csv_export_is_admin_only_and_escapes_spreadsheet_formulas(admin_client):
    client, factory = admin_client
    create_user(factory, "admin@example.com", "admin")
    reviewer_id = create_user(factory, "=SUM(1,1)@example.com", "reviewer")
    base = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    add_audit(
        factory,
        audit_id="audit000005",
        created_at=base,
        status="completed",
        verdict="manual_review_required",
        review_status="completed",
        reviewer_user_id=reviewer_id,
        reviewed_by_user_id=reviewer_id,
        human_decision="rejected",
        completed_at=base - timedelta(minutes=2),
        reviewed_at=base,
    )

    response = client.get(
        "/api/v1/admin/audits.csv?date_from=2026-11-05&date_to=2026-11-05",
        headers=login(client, "admin@example.com"),
    )
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "audit_id,video_id,content_type" in response.text
    assert "'=SUM(1,1)@example.com" in response.text
    assert "youtube_url" not in response.text
    assert client.get("/api/v1/admin/audits.csv").status_code == 401
