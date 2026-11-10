from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_workspace_access
from app.models.audit import AuditRecord
from app.schemas.audit import AuditCreate, AuditResponse

router = APIRouter(dependencies=[Depends(require_workspace_access)])


@router.post("", response_model=AuditResponse, status_code=status.HTTP_202_ACCEPTED)
def create_audit(
    payload: AuditCreate,
    db: Session = Depends(get_db),
) -> AuditRecord:
    """Queue a video audit in the shared workspace."""
    now = datetime.now(timezone.utc)
    record = AuditRecord(
        youtube_url=payload.youtube_url,
        video_id=payload.video_id,
        content_type=payload.content_type.value,
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
    limit: int = Query(default=30, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[AuditRecord]:
    statement = (
        select(AuditRecord)
        .order_by(AuditRecord.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(statement).all())


@router.post("/{audit_id}/cancel", response_model=AuditResponse)
def cancel_audit(
    audit_id: str,
    db: Session = Depends(get_db),
) -> AuditRecord:
    """Cancel a queued job or request cooperative worker cancellation."""
    record = db.scalar(
        select(AuditRecord)
        .where(AuditRecord.id == audit_id)
        .with_for_update()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found")

    now = datetime.now(timezone.utc)
    if record.status == "pending":
        record.status = "cancelled"
        record.processing_stage = None
        record.error_message = "Анализ отменён участником команды."
        record.next_retry_at = None
        record.completed_at = now
        record.updated_at = now
    elif record.status == "processing":
        record.status = "cancelling"
        record.error_message = "Запрошена отмена анализа участником команды."
        record.updated_at = now
    elif record.status not in {"cancelling", "cancelled"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя отменить уже завершённый анализ.",
        )

    db.commit()
    db.refresh(record)
    return record


@router.get("/{audit_id}", response_model=AuditResponse)
def get_audit(
    audit_id: str,
    db: Session = Depends(get_db),
) -> AuditRecord:
    record = db.get(AuditRecord, audit_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Audit record not found")
    return record
