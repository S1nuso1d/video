from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_workspace_access
from app.models.audit import AuditRecord
from app.models.review import HumanReviewEvent
from app.schemas.audit import AuditResponse
from app.schemas.reviews import (
    HumanReviewDecisionRequest,
    HumanReviewEventResponse,
    ReviewReopenRequest,
    ReviewStatus,
)

router = APIRouter(dependencies=[Depends(require_workspace_access)])


def _get_audit_for_update(db: Session, audit_id: str) -> AuditRecord:
    record = db.scalar(
        select(AuditRecord)
        .where(AuditRecord.id == audit_id)
        .with_for_update()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found")
    return record


def _ensure_reviewable(record: AuditRecord) -> None:
    if record.status != "completed" or record.review_status not in {"pending", "assigned"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Анализ не ожидает командного решения",
        )


def _event(
    *,
    audit_id: str,
    action: str,
    from_status: str | None,
    to_status: str | None,
    decision: str | None = None,
    comment: str | None = None,
) -> HumanReviewEvent:
    # actor_user_id remains nullable for compatibility with historical records.
    return HumanReviewEvent(
        audit_id=audit_id,
        actor_user_id=None,
        action=action,
        from_status=from_status,
        to_status=to_status,
        decision=decision,
        comment=comment,
        created_at=datetime.now(timezone.utc),
    )


@router.get("/queue", response_model=list[AuditResponse])
def list_review_queue(
    review_status: ReviewStatus | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[AuditRecord]:
    statement = select(AuditRecord).where(
        AuditRecord.review_status != "not_required"
    )
    if review_status is None:
        statement = statement.where(
            AuditRecord.review_status.in_(["pending", "assigned"])
        )
    else:
        statement = statement.where(AuditRecord.review_status == review_status.value)
    statement = statement.order_by(
        case(
            (AuditRecord.review_status == "pending", 0),
            (AuditRecord.review_status == "assigned", 1),
            else_=2,
        ),
        AuditRecord.created_at.asc(),
    ).offset(offset).limit(limit)
    return list(db.scalars(statement).all())


@router.get("/{audit_id}/history", response_model=list[HumanReviewEventResponse])
def get_review_history(
    audit_id: str,
    db: Session = Depends(get_db),
) -> list[HumanReviewEvent]:
    exists = db.scalar(select(AuditRecord.id).where(AuditRecord.id == audit_id))
    if exists is None:
        raise HTTPException(status_code=404, detail="Audit record not found")
    statement = (
        select(HumanReviewEvent)
        .where(HumanReviewEvent.audit_id == audit_id)
        .order_by(HumanReviewEvent.created_at.asc(), HumanReviewEvent.id.asc())
    )
    return list(db.scalars(statement).all())


@router.post("/{audit_id}/decision", response_model=AuditResponse)
def submit_review_decision(
    audit_id: str,
    payload: HumanReviewDecisionRequest,
    db: Session = Depends(get_db),
) -> AuditRecord:
    """Record a shared team's decision without tying it to an individual account."""
    record = _get_audit_for_update(db, audit_id)
    _ensure_reviewable(record)

    previous_status = record.review_status
    now = datetime.now(timezone.utc)
    record.human_review_decision = payload.decision.value
    record.human_review_comment = payload.comment
    record.human_reviewed_by_user_id = None
    record.human_reviewed_at = now
    record.reviewer_user_id = None
    record.review_status = "completed"
    db.add(
        _event(
            audit_id=record.id,
            action="decision_submitted",
            from_status=previous_status,
            to_status="completed",
            decision=payload.decision.value,
            comment=payload.comment,
        )
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/{audit_id}/reopen", response_model=AuditResponse)
def reopen_review(
    audit_id: str,
    payload: ReviewReopenRequest,
    db: Session = Depends(get_db),
) -> AuditRecord:
    """Reopen a team decision while preserving its previous event in history."""
    record = _get_audit_for_update(db, audit_id)
    if record.status != "completed" or record.review_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Повторно открыть можно только завершённую ручную проверку",
        )
    previous_decision = record.human_review_decision
    record.review_status = "pending"
    record.reviewer_user_id = None
    record.human_review_decision = None
    record.human_review_comment = None
    record.human_reviewed_by_user_id = None
    record.human_reviewed_at = None
    db.add(
        _event(
            audit_id=record.id,
            action="reopened",
            from_status="completed",
            to_status="pending",
            decision=previous_decision,
            comment=payload.comment,
        )
    )
    db.commit()
    db.refresh(record)
    return record
