from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ReviewStatus(str, Enum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    ASSIGNED = "assigned"  # Accepted for historical records created by older versions.
    COMPLETED = "completed"


class HumanReviewDecision(str, Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    NEEDS_CHANGES = "needs_changes"


class HumanReviewDecisionRequest(BaseModel):
    decision: HumanReviewDecision
    comment: str | None = Field(default=None, max_length=2000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def require_reason_for_negative_outcomes(self):
        if self.decision in {
            HumanReviewDecision.REJECTED,
            HumanReviewDecision.NEEDS_CHANGES,
        } and (self.comment is None or len(self.comment) < 10):
            raise ValueError(
                "A comment of at least 10 characters is required for rejection "
                "or a request for changes"
            )
        return self


class ReviewReopenRequest(BaseModel):
    comment: str = Field(min_length=5, max_length=2000)

    @field_validator("comment")
    @classmethod
    def normalize_comment(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 5:
            raise ValueError("Укажите причину повторного открытия проверки")
        return value


class HumanReviewEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    audit_id: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    decision: str | None = None
    comment: str | None = None
    created_at: datetime
