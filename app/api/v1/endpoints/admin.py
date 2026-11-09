import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from app.core.database import get_db
from app.core.security import require_roles
from app.models.audit import AuditRecord
from app.models.user import UserAccount
from app.schemas.admin import (
    AdminOverviewResponse,
    AdminUserResponse,
    AdminUserUpdate,
    DailyAuditMetrics,
    ReviewerPerformance,
)

router = APIRouter()


def _date_window(date_from: date | None, date_to: date | None) -> tuple[date, date, datetime, datetime]:
    today = datetime.now(timezone.utc).date()
    end_day = date_to or today
    start_day = date_from or (end_day - timedelta(days=29))
    if start_day > end_day:
        raise HTTPException(
            status_code=422,
            detail="date_from не может быть позже date_to",
        )
    if (end_day - start_day).days > 366:
        raise HTTPException(
            status_code=422,
            detail="Максимальный период отчёта — 367 календарных дней",
        )
    start_at = datetime.combine(start_day, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end_day + timedelta(days=1), time.min, tzinfo=timezone.utc)
    return start_day, end_day, start_at, end_at


def _filters(start_at: datetime, end_at: datetime) -> tuple[Any, Any]:
    return (
        AuditRecord.created_at >= start_at,
        AuditRecord.created_at < end_at,
    )


def _count_by(db: Session, column: Any, filters: tuple[Any, ...]) -> dict[str, int]:
    rows = db.execute(
        select(column, func.count())
        .where(*filters)
        .group_by(column)
    ).all()
    return {str(value or "unknown"): int(count) for value, count in rows}


def _aggregate_tokens(db: Session, filters: tuple[Any, ...]) -> tuple[dict[str, int], int]:
    token_names = (
        "input_tokens",
        "output_tokens",
        "thought_tokens",
        "cached_input_tokens",
        "total_tokens",
    )
    totals = {name: 0 for name in token_names}
    requests = 0
    rows = db.scalars(
        select(AuditRecord.gemini_usage).where(*filters)
    ).all()
    for usage in rows:
        if not isinstance(usage, dict):
            continue
        for name in token_names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                totals[name] += int(value)
        value = usage.get("request_count")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            requests += int(value)
    return totals, requests


def _reviewer_metrics(
    db: Session,
    start_at: datetime,
    end_at: datetime,
) -> list[ReviewerPerformance]:
    reviewer_users = list(
        db.scalars(
            select(UserAccount)
            .where(UserAccount.role == "reviewer")
            .order_by(UserAccount.email.asc())
        ).all()
    )
    reviewer_ids = {user.id for user in reviewer_users}
    if not reviewer_ids:
        return []

    open_rows = db.execute(
        select(AuditRecord.reviewer_user_id, func.count())
        .where(
            AuditRecord.review_status == "assigned",
            AuditRecord.reviewer_user_id.in_(reviewer_ids),
        )
        .group_by(AuditRecord.reviewer_user_id)
    ).all()
    open_by_reviewer = {user_id: int(count) for user_id, count in open_rows}

    completed_rows = db.execute(
        select(
            AuditRecord.human_reviewed_by_user_id,
            AuditRecord.human_review_decision,
            AuditRecord.completed_at,
            AuditRecord.human_reviewed_at,
        )
        .where(
            AuditRecord.review_status == "completed",
            AuditRecord.human_reviewed_by_user_id.in_(reviewer_ids),
            AuditRecord.human_reviewed_at >= start_at,
            AuditRecord.human_reviewed_at < end_at,
        )
    ).all()

    stats: dict[str, dict[str, Any]] = {
        user_id: {
            "completed_reviews": 0,
            "approved": 0,
            "rejected": 0,
            "needs_changes": 0,
            "durations": [],
        }
        for user_id in reviewer_ids
    }
    for user_id, decision, completed_at, reviewed_at in completed_rows:
        item = stats.get(user_id)
        if item is None:
            continue
        item["completed_reviews"] += 1
        if decision in {"approved", "rejected", "needs_changes"}:
            item[decision] += 1
        if completed_at is not None and reviewed_at is not None:
            # SQLite returns naive datetime values for timezone-aware columns;
            # subtraction is still valid because both values share that storage.
            seconds = (reviewed_at - completed_at).total_seconds()
            if seconds >= 0:
                item["durations"].append(seconds)

    result = []
    for user in reviewer_users:
        item = stats[user.id]
        durations = item["durations"]
        result.append(
            ReviewerPerformance(
                user_id=user.id,
                email=user.email,
                assigned_open=open_by_reviewer.get(user.id, 0),
                completed_reviews=item["completed_reviews"],
                approved=item["approved"],
                rejected=item["rejected"],
                needs_changes=item["needs_changes"],
                average_queue_seconds=(
                    round(sum(durations) / len(durations), 2) if durations else None
                ),
            )
        )
    return result


@router.get("/overview", response_model=AdminOverviewResponse)
def get_admin_overview(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: UserAccount = Depends(require_roles("admin")),
) -> AdminOverviewResponse:
    start_day, end_day, start_at, end_at = _date_window(date_from, date_to)
    filters = _filters(start_at, end_at)

    total = int(db.scalar(
        select(func.count()).select_from(AuditRecord).where(*filters)
    ) or 0)
    statuses = _count_by(db, AuditRecord.status, filters)
    verdicts = _count_by(db, AuditRecord.verdict, filters)
    criteria = _count_by(db, AuditRecord.decision, filters)
    review_statuses = _count_by(db, AuditRecord.review_status, filters)
    human_decisions = _count_by(db, AuditRecord.human_review_decision, filters)

    completed = statuses.get("completed", 0)
    failed = statuses.get("failed", 0)
    completion_rate = round(completed / total * 100, 2) if total else 0.0
    failure_rate = round(failed / total * 100, 2) if total else 0.0

    cost_usd = db.scalar(
        select(func.sum(AuditRecord.gemini_cost_usd)).where(*filters)
    )
    cost_rub = db.scalar(
        select(func.sum(AuditRecord.gemini_cost_rub)).where(*filters)
    )
    token_usage, gemini_requests = _aggregate_tokens(db, filters)
    total_attempts = int(db.scalar(
        select(func.coalesce(func.sum(AuditRecord.attempt_count), 0)).where(*filters)
    ) or 0)

    daily_rows = db.execute(
        select(
            func.date(AuditRecord.created_at),
            func.count(),
            func.sum(case((AuditRecord.status == "completed", 1), else_=0)),
            func.sum(case((AuditRecord.status == "failed", 1), else_=0)),
            func.sum(case((AuditRecord.review_status != "not_required", 1), else_=0)),
        )
        .where(*filters)
        .group_by(func.date(AuditRecord.created_at))
        .order_by(func.date(AuditRecord.created_at))
    ).all()
    daily = [
        DailyAuditMetrics(
            date=date.fromisoformat(str(day)),
            total=int(day_total),
            completed=int(day_completed or 0),
            failed=int(day_failed or 0),
            sent_to_review=int(day_review or 0),
        )
        for day, day_total, day_completed, day_failed, day_review in daily_rows
    ]

    return AdminOverviewResponse(
        date_from=start_day,
        date_to=end_day,
        generated_at=datetime.now(timezone.utc),
        total_audits=total,
        audits_by_status=statuses,
        machine_verdicts=verdicts,
        criteria_decisions=criteria,
        human_review_statuses=review_statuses,
        human_review_decisions=human_decisions,
        completion_rate_percent=completion_rate,
        failure_rate_percent=failure_rate,
        gemini_estimated_cost_usd=float(cost_usd) if cost_usd is not None else None,
        gemini_estimated_cost_rub=float(cost_rub) if cost_rub is not None else None,
        gemini_requests=gemini_requests,
        token_usage=token_usage,
        total_attempts=total_attempts,
        daily=daily,
        reviewers=_reviewer_metrics(db, start_at, end_at),
    )


@router.get("/audits.csv")
def export_audits_csv(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    db: Session = Depends(get_db),
    _admin: UserAccount = Depends(require_roles("admin")),
) -> Response:
    start_day, end_day, start_at, end_at = _date_window(date_from, date_to)
    reviewer = aliased(UserAccount)
    rows = db.execute(
        select(
            AuditRecord.id,
            AuditRecord.video_id,
            AuditRecord.content_type,
            AuditRecord.created_at,
            AuditRecord.status,
            AuditRecord.verdict,
            AuditRecord.decision,
            AuditRecord.review_status,
            AuditRecord.human_review_decision,
            AuditRecord.human_reviewed_at,
            reviewer.email,
            AuditRecord.attempt_count,
            AuditRecord.gemini_cost_usd,
            AuditRecord.gemini_cost_rub,
        )
        .outerjoin(reviewer, reviewer.id == AuditRecord.human_reviewed_by_user_id)
        .where(*_filters(start_at, end_at))
        .order_by(AuditRecord.created_at.asc())
    ).all()

    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow([
        "audit_id",
        "video_id",
        "content_type",
        "created_at_utc",
        "processing_status",
        "gemini_verdict",
        "criteria_decision",
        "review_status",
        "human_decision",
        "human_reviewed_at_utc",
        "reviewer_email",
        "attempt_count",
        "gemini_estimated_cost_usd",
        "gemini_estimated_cost_rub",
    ])

    def safe_cell(value: Any) -> Any:
        if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
            return "'" + value
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    for row in rows:
        writer.writerow([safe_cell(value) for value in row])

    filename = f"video-auditor-audits-{start_day.isoformat()}-{end_day.isoformat()}.csv"
    return Response(
        content="\ufeff" + output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/users", response_model=list[AdminUserResponse])
def list_admin_users(
    db: Session = Depends(get_db),
    _admin: UserAccount = Depends(require_roles("admin")),
) -> list[UserAccount]:
    return list(
        db.scalars(select(UserAccount).order_by(UserAccount.created_at.asc())).all()
    )


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
def update_admin_user(
    user_id: str,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: UserAccount = Depends(require_roles("admin")),
) -> UserAccount:
    target = db.scalar(
        select(UserAccount).where(UserAccount.id == user_id).with_for_update()
    )
    if target is None:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    next_role = payload.role.value if payload.role is not None else target.role
    next_active = payload.is_active if payload.is_active is not None else target.is_active
    if target.id == admin.id and (next_role != "admin" or not next_active):
        raise HTTPException(
            status_code=409,
            detail="Нельзя отключить или понизить роль собственной учётной записи",
        )

    if target.is_active and target.role == "admin" and (
        next_role != "admin" or not next_active
    ):
        active_admins = list(
            db.scalars(
                select(UserAccount)
                .where(UserAccount.role == "admin", UserAccount.is_active.is_(True))
                .with_for_update()
            ).all()
        )
        if len(active_admins) <= 1:
            raise HTTPException(
                status_code=409,
                detail="Нельзя отключить последнего активного администратора",
            )

    target.role = next_role
    target.is_active = next_active
    db.commit()
    db.refresh(target)
    return target
