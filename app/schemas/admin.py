from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.auth import UserRole


class AdminUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: UserRole
    is_active: bool
    created_at: datetime
    last_login_at: datetime | None = None


class AdminUserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: UserRole | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def require_a_change(self):
        if self.role is None and self.is_active is None:
            raise ValueError("Укажите роль или состояние учётной записи")
        return self


class DailyAuditMetrics(BaseModel):
    date: date
    total: int
    completed: int
    failed: int
    sent_to_review: int


class ReviewerPerformance(BaseModel):
    user_id: str
    email: str
    assigned_open: int = 0
    completed_reviews: int = 0
    approved: int = 0
    rejected: int = 0
    needs_changes: int = 0
    average_queue_seconds: float | None = None


class AdminOverviewResponse(BaseModel):
    date_from: date
    date_to: date
    generated_at: datetime
    total_audits: int
    audits_by_status: dict[str, int]
    machine_verdicts: dict[str, int]
    criteria_decisions: dict[str, int]
    human_review_statuses: dict[str, int]
    human_review_decisions: dict[str, int]
    completion_rate_percent: float
    failure_rate_percent: float
    gemini_estimated_cost_usd: float | None = None
    gemini_estimated_cost_rub: float | None = None
    gemini_requests: int = 0
    token_usage: dict[str, int]
    total_attempts: int = 0
    daily: list[DailyAuditMetrics]
    reviewers: list[ReviewerPerformance]
