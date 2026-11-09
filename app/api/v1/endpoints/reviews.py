from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.audit import AuditRecord
from app.models.review import HumanReviewEvent
from app.models.user import UserAccount
from app.schemas.audit import AuditResponse
from app.schemas.reviews import (
    HumanReviewDecisionRequest,
    HumanReviewEventResponse,
    ReviewAssignmentRequest,
    ReviewReopenRequest,
    ReviewStatus,
)

router = APIRouter()


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
            detail="Audit is not waiting for a human review decision",
        )


def _event(
    *,
    audit_id: str,
    actor_user_id: str | None,
    action: str,
    from_status: str | None,
    to_status: str | None,
    decision: str | None = None,
    comment: str | None = None,
) -> HumanReviewEvent:
    return HumanReviewEvent(
        audit_id=audit_id,
        actor_user_id=actor_user_id,
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
    assigned_to_me: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(require_roles("admin", "reviewer")),
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
    if assigned_to_me:
        statement = statement.where(AuditRecord.reviewer_user_id == user.id)
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
    _user: UserAccount = Depends(require_roles("admin", "reviewer")),
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


@router.put("/{audit_id}/assignment", response_model=AuditResponse)
def assign_reviewer(
    audit_id: str,
    payload: ReviewAssignmentRequest,
    db: Session = Depends(get_db),
    admin: UserAccount = Depends(require_roles("admin")),
) -> AuditRecord:
    # Authentication has already queried the user on this Session, so its
    # transaction is active. Keep the row lock, write the event, and commit once.
    record = _get_audit_for_update(db, audit_id)
    _ensure_reviewable(record)

    reviewer: UserAccount | None = None
    if payload.reviewer_user_id is not None:
        reviewer = db.get(UserAccount, payload.reviewer_user_id)
        if reviewer is None or not reviewer.is_active or reviewer.role != "reviewer":
            raise HTTPException(
                status_code=422,
                detail="Указанный пользователь не является активным reviewer",
            )

    previous_user_id = record.reviewer_user_id
    previous_status = record.review_status
    next_user_id = reviewer.id if reviewer is not None else None
    next_status = "assigned" if reviewer is not None else "pending"
    if previous_user_id == next_user_id and previous_status == next_status:
        return record

    record.reviewer_user_id = next_user_id
    record.review_status = next_status
    db.add(
        _event(
            audit_id=record.id,
            actor_user_id=admin.id,
            action="assigned" if reviewer is not None else "unassigned",
            from_status=previous_status,
            to_status=next_status,
            comment=payload.comment,
        )
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/{audit_id}/claim", response_model=AuditResponse)
def claim_review(
    audit_id: str,
    db: Session = Depends(get_db),
    reviewer: UserAccount = Depends(require_roles("reviewer")),
) -> AuditRecord:
    record = _get_audit_for_update(db, audit_id)
    _ensure_reviewable(record)
    if record.review_status != "pending" or record.reviewer_user_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Audit is already assigned or is not available to claim",
        )
    record.reviewer_user_id = reviewer.id
    record.review_status = "assigned"
    db.add(
        _event(
            audit_id=record.id,
            actor_user_id=reviewer.id,
            action="claimed",
            from_status="pending",
            to_status="assigned",
        )
    )
    db.commit()
    db.refresh(record)
    return record


@router.post("/{audit_id}/decision", response_model=AuditResponse)
def submit_review_decision(
    audit_id: str,
    payload: HumanReviewDecisionRequest,
    db: Session = Depends(get_db),
    user: UserAccount = Depends(require_roles("admin", "reviewer")),
) -> AuditRecord:
    record = _get_audit_for_update(db, audit_id)
    _ensure_reviewable(record)
    if user.role == "reviewer" and record.reviewer_user_id != user.id:
        if record.reviewer_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Сначала возьмите аудит в работу",
            )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Аудит назначен другому reviewer",
        )

    previous_status = record.review_status
    now = datetime.now(timezone.utc)
    record.human_review_decision = payload.decision.value
    record.human_review_comment = payload.comment
    record.human_reviewed_by_user_id = user.id
    record.human_reviewed_at = now
    record.review_status = "completed"
    db.add(
        _event(
            audit_id=record.id,
            actor_user_id=user.id,
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
    admin: UserAccount = Depends(require_roles("admin")),
) -> AuditRecord:
    record = _get_audit_for_update(db, audit_id)
    if record.status != "completed" or record.review_status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Only a completed human review can be reopened",
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
            actor_user_id=admin.id,
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
