from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_roles
from app.models.audit import AuditRecord
from app.models.user import UserAccount
from app.schemas.audit import AuditCreate, AuditResponse

router = APIRouter()


@router.post("", response_model=AuditResponse, status_code=status.HTTP_202_ACCEPTED)
def create_audit(
    payload: AuditCreate,
    db: Session = Depends(get_db),
    user: UserAccount = Depends(require_roles("admin", "operator")),
) -> AuditRecord:
    """Queue a video audit. A separate worker must be running to process it."""
    now = datetime.now(timezone.utc)
    record = AuditRecord(
        youtube_url=payload.youtube_url,
        video_id=payload.video_id,
        content_type=payload.content_type.value,
        created_by_user_id=user.id,
        criteria_spec=(
            payload.criteria_spec.model_dump(mode="json")
            if payload.criteria_spec is not None
            else None
        ),
        status="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.get("", response_model=list[AuditResponse])
def list_audits(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    user: UserAccount = Depends(require_roles("admin", "operator", "reviewer")),
) -> list[AuditRecord]:
    statement = select(AuditRecord).order_by(AuditRecord.created_at.desc())
    if user.role == "operator":
        statement = statement.where(AuditRecord.created_by_user_id == user.id)
    statement = statement.offset(offset).limit(limit)
    return list(db.scalars(statement).all())


@router.get("/{audit_id}", response_model=AuditResponse)
def get_audit(
    audit_id: str,
    db: Session = Depends(get_db),
    user: UserAccount = Depends(require_roles("admin", "operator", "reviewer")),
) -> AuditRecord:
    record = db.get(AuditRecord, audit_id)
    if record is None or (
        user.role == "operator" and record.created_by_user_id != user.id
    ):
        # Do not disclose whether another operator's audit ID exists.
        raise HTTPException(status_code=404, detail="Audit record not found")
    return record
