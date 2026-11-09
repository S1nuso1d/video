import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import case, select, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.audit import AuditRecord
from app.services.gemini_auditor import (
    GeminiAuditError,
    GeminiAuditResult,
    GeminiConfigurationError,
    analyze_youtube_video,
)
from app.services.gemini_costs import (
    aggregate_processing_metrics,
    aggregate_usage,
    build_usage_attempt,
)
from app.services.video_downloader import (
    DownloadedVideo,
    VideoDownloadError,
    cleanup_audit_temp_dir,
    download_video,
)
from app.services.video_probe import (
    FFprobeUnavailableError,
    VideoProbeError,
    probe_video,
)
from app.services.criteria_engine import evaluate_criteria
from app.services.frame_analyzer import (
    OpenCVUnavailableError,
    VideoFrameAnalysisError,
    analyze_video_frames,
)

logger = logging.getLogger(__name__)


def process_audit(audit_id: str, lease_token: str) -> None:
    """Process an audit only while the supplied worker lease remains valid."""
    with SessionLocal() as db:
        record = db.scalar(
            select(AuditRecord).where(
                AuditRecord.id == audit_id,
                AuditRecord.status == "processing",
                AuditRecord.lease_token == lease_token,
            )
        )
        if record is None:
            logger.warning(
                "Skipping audit %s because this worker no longer owns its lease",
                audit_id,
            )
            return
        youtube_url = record.youtube_url
        content_type = record.content_type
        criteria_spec = record.criteria_spec
        current_attempt = int(record.attempt_count or 0)
        previous_usage = record.gemini_usage
        previous_metrics = record.processing_metrics

    downloaded: DownloadedVideo | None = None
    attempt_started = time.perf_counter()
    active_stage: str | None = None
    stage_started: float | None = None
    stage_durations: dict[str, float] = {}
    attempt_usage: dict[str, int | None] | None = None
    request_made = False
    try:
        logger.info("Downloading source video for audit %s", audit_id)
        active_stage = "download"
        stage_started = time.perf_counter()
        downloaded = download_video(youtube_url, audit_id)
        stage_durations["download"] = round(time.perf_counter() - stage_started, 3)
        active_stage = None
        stage_started = None
        downloaded_size = downloaded.path.stat().st_size
        logger.info(
            "Downloaded audit %s to temporary file (%s bytes)",
            audit_id,
            downloaded_size,
        )

        try:
            active_stage = "ffprobe"
            stage_started = time.perf_counter()
            probe_result = probe_video(downloaded.path)
            technical_metadata = probe_result.metadata
            technical_findings = probe_result.findings
            logger.info(
                "FFprobe inspected audit %s: duration=%.3fs size=%s bytes",
                audit_id,
                technical_metadata["duration_seconds"],
                downloaded_size,
            )
        except FFprobeUnavailableError as exc:
            if settings.ffprobe_required:
                raise
            technical_metadata = None
            technical_findings = [
                {
                    "code": "ffprobe_unavailable",
                    "severity": "warning",
                    "source": "system",
                    "confidence": "high",
                    "description": str(exc),
                    "evidence": {},
                }
            ]
            logger.warning("Skipping technical metadata for audit %s: %s", audit_id, exc)
        finally:
            if active_stage == "ffprobe" and stage_started is not None:
                stage_durations["ffprobe"] = round(
                    time.perf_counter() - stage_started, 3
                )
                active_stage = None
                stage_started = None

        try:
            active_stage = "opencv"
            stage_started = time.perf_counter()
            frame_result = analyze_video_frames(
                downloaded.path,
                duration_seconds=(
                    technical_metadata.get("duration_seconds")
                    if technical_metadata
                    else None
                ),
            )
            visual_metadata = frame_result.metadata
            visual_findings = frame_result.findings
            logger.info(
                "OpenCV sampled %s frames for audit %s and found %s visual observations",
                visual_metadata.get("sampled_frames", 0),
                audit_id,
                len(visual_findings),
            )
        except VideoFrameAnalysisError as exc:
            if settings.cv_analysis_required:
                raise
            visual_metadata = None
            visual_code = (
                "opencv_unavailable"
                if isinstance(exc, OpenCVUnavailableError)
                else "opencv_analysis_failed"
            )
            visual_findings = [
                {
                    "code": visual_code,
                    "severity": "warning",
                    "source": "system",
                    "confidence": "high",
                    "description": str(exc),
                    "evidence": {},
                }
            ]
            logger.warning(
                "Skipping OpenCV frame analysis for audit %s: %s", audit_id, exc
            )
        finally:
            if active_stage == "opencv" and stage_started is not None:
                stage_durations["opencv"] = round(
                    time.perf_counter() - stage_started, 3
                )
                active_stage = None
                stage_started = None

        # Persist deterministic evidence before the expensive AI request. The
        # update is fenced so an expired worker cannot attach evidence to a new attempt.
        with SessionLocal() as db:
            saved = db.execute(
                update(AuditRecord)
                .where(
                    AuditRecord.id == audit_id,
                    AuditRecord.status == "processing",
                    AuditRecord.lease_token == lease_token,
                )
                .values(
                    technical_metadata=technical_metadata,
                    technical_findings=technical_findings,
                    visual_metadata=visual_metadata,
                    visual_findings=visual_findings,
                    updated_at=datetime.now(timezone.utc),
                )
            )
            db.commit()
            if saved.rowcount != 1:
                logger.warning(
                    "Stopping audit %s because its lease changed before AI analysis",
                    audit_id,
                )
                return

        active_stage = "gemini"
        stage_started = time.perf_counter()
        gemini_result = analyze_youtube_video(
            youtube_url,
            content_type,
            local_video_path=downloaded.path,
        )
        stage_durations["gemini"] = round(time.perf_counter() - stage_started, 3)
        active_stage = None
        stage_started = None
        if isinstance(gemini_result, GeminiAuditResult):
            report = gemini_result.report
            attempt_usage = gemini_result.usage
            request_made = gemini_result.request_made
        else:
            # Keep processor integrations and existing test doubles compatible.
            report = gemini_result
        report_dict = report.model_dump(mode="json")
        criteria_results = evaluate_criteria(
            criteria_spec,
            technical_metadata=technical_metadata,
            visual_metadata=visual_metadata,
            visual_findings=visual_findings,
            report=report_dict,
        )
        project_decision = (
            criteria_results.get("decision") if criteria_results is not None else None
        )
        decision_summary = (
            criteria_results.get("summary") if criteria_results is not None else None
        )
        usage_attempt = build_usage_attempt(
            attempt_usage,
            request_made=request_made,
            attempt_number=current_attempt,
            model=settings.gemini_model,
            pricing_version=settings.gemini_pricing_version,
            input_price_usd_per_million=settings.gemini_input_price_usd_per_million,
            cached_input_price_usd_per_million=settings.gemini_cached_input_price_usd_per_million,
            output_price_usd_per_million=settings.gemini_output_price_usd_per_million,
            usd_to_rub_rate=settings.gemini_usd_to_rub_rate,
        )
        usage_summary = aggregate_usage(previous_usage, usage_attempt)
        processing_metrics = aggregate_processing_metrics(
            previous_metrics,
            attempt_number=current_attempt,
            stage_durations=stage_durations,
            total_seconds=time.perf_counter() - attempt_started,
        )
        now = datetime.now(timezone.utc)
        with SessionLocal() as db:
            result = db.execute(
                update(AuditRecord)
                .where(
                    AuditRecord.id == audit_id,
                    AuditRecord.status == "processing",
                    AuditRecord.lease_token == lease_token,
                )
                .values(
                    report=report_dict,
                    verdict=report.verdict.value,
                    summary=report.summary,
                    detected_defects=[
                        item.model_dump(mode="json") for item in report.detected_defects
                    ],
                    markup_comment=report.markup_comment,
                    criteria_results=criteria_results,
                    decision=project_decision,
                    decision_summary=decision_summary,
                    gemini_usage=usage_summary,
                    gemini_cost_usd=(
                        usage_summary.get("estimated_cost_usd")
                        if usage_summary is not None
                        else None
                    ),
                    gemini_cost_rub=(
                        usage_summary.get("estimated_cost_rub")
                        if usage_summary is not None
                        else None
                    ),
                    processing_metrics=processing_metrics,
                    status="completed",
                    error_message=None,
                    next_retry_at=None,
                    heartbeat_at=None,
                    worker_id=None,
                    lease_token=None,
                    completed_at=now,
                    updated_at=now,
                )
            )
            db.commit()
            if result.rowcount != 1:
                logger.warning(
                    "Discarded result for audit %s because the worker lease expired",
                    audit_id,
                )
                return

        logger.info(
            "Audit %s completed with verdict=%s project_decision=%s",
            audit_id,
            report.verdict.value,
            project_decision or "not_configured",
        )
    except Exception as exc:
        if active_stage is not None and stage_started is not None:
            stage_durations[active_stage] = round(
                time.perf_counter() - stage_started, 3
            )
        if isinstance(exc, GeminiAuditError):
            attempt_usage = exc.usage
            request_made = exc.request_made
        usage_attempt = build_usage_attempt(
            attempt_usage,
            request_made=request_made,
            attempt_number=current_attempt,
            model=settings.gemini_model,
            pricing_version=settings.gemini_pricing_version,
            input_price_usd_per_million=settings.gemini_input_price_usd_per_million,
            cached_input_price_usd_per_million=settings.gemini_cached_input_price_usd_per_million,
            output_price_usd_per_million=settings.gemini_output_price_usd_per_million,
            usd_to_rub_rate=settings.gemini_usd_to_rub_rate,
        )
        usage_summary = aggregate_usage(previous_usage, usage_attempt)
        processing_metrics = aggregate_processing_metrics(
            previous_metrics,
            attempt_number=current_attempt,
            stage_durations=stage_durations,
            total_seconds=time.perf_counter() - attempt_started,
        )
        now = datetime.now(timezone.utc)
        retryable = isinstance(exc, GeminiAuditError) and exc.retryable
        rate_limited = isinstance(exc, GeminiAuditError) and exc.status_code == 429
        max_attempts = max(1, int(settings.audit_max_attempts))
        will_retry = retryable and current_attempt < max_attempts

        if isinstance(
            exc,
            (
                GeminiAuditError,
                GeminiConfigurationError,
                VideoDownloadError,
                VideoProbeError,
                VideoFrameAnalysisError,
            ),
        ):
            safe_message = str(exc)[:500]
        else:
            safe_message = "Не удалось завершить анализ. Проверьте настройки и журнал Worker."

        cooldown_seconds = max(30, int(settings.gemini_rate_limit_cooldown_seconds))
        base_delay = max(1, int(settings.audit_retry_delay_seconds)) * (
            2 ** max(0, current_attempt - 1)
        )
        delay_seconds = max(base_delay, cooldown_seconds) if rate_limited else base_delay
        next_retry_at = now + timedelta(seconds=delay_seconds) if will_retry else None

        with SessionLocal() as db:
            result = db.execute(
                update(AuditRecord)
                .where(
                    AuditRecord.id == audit_id,
                    AuditRecord.status == "processing",
                    AuditRecord.lease_token == lease_token,
                )
                .values(
                    error_message=safe_message,
                    gemini_usage=usage_summary,
                    gemini_cost_usd=(
                        usage_summary.get("estimated_cost_usd")
                        if usage_summary is not None
                        else None
                    ),
                    gemini_cost_rub=(
                        usage_summary.get("estimated_cost_rub")
                        if usage_summary is not None
                        else None
                    ),
                    processing_metrics=processing_metrics,
                    updated_at=now,
                    status="pending" if will_retry else "failed",
                    next_retry_at=next_retry_at,
                    completed_at=None if will_retry else now,
                    started_at=None if will_retry else AuditRecord.started_at,
                    heartbeat_at=None,
                    worker_id=None,
                    lease_token=None,
                )
            )
            still_owner = result.rowcount == 1

            if still_owner and rate_limited:
                cooldown_until = now + timedelta(seconds=cooldown_seconds)
                deferred_retry_at = case(
                    (AuditRecord.next_retry_at.is_(None), cooldown_until),
                    (AuditRecord.next_retry_at < cooldown_until, cooldown_until),
                    else_=AuditRecord.next_retry_at,
                )
                deferred = db.execute(
                    update(AuditRecord)
                    .where(
                        AuditRecord.status == "pending",
                        AuditRecord.id != audit_id,
                    )
                    .values(
                        next_retry_at=deferred_retry_at,
                        updated_at=now,
                    )
                )
                logger.warning(
                    "Gemini HTTP 429: deferred %s queued audits for at least %s seconds",
                    int(deferred.rowcount or 0),
                    cooldown_seconds,
                )
            db.commit()

        if not still_owner:
            logger.warning(
                "Ignored failure for audit %s because this worker no longer owns its lease",
                audit_id,
            )
        elif retryable and will_retry:
            logger.warning(
                "Audit %s attempt %s/%s failed; retry scheduled at %s: %s",
                audit_id,
                current_attempt,
                max_attempts,
                next_retry_at.isoformat() if next_retry_at else "n/a",
                safe_message,
            )
        elif retryable:
            logger.warning(
                "Audit %s exhausted retry budget at attempt %s/%s: %s",
                audit_id,
                current_attempt,
                max_attempts,
                safe_message,
            )
        else:
            logger.exception("Audit %s attempt %s failed", audit_id, current_attempt)
    finally:
        if downloaded is not None:
            cleanup_audit_temp_dir(downloaded.work_dir)
            logger.info("Cleaned temporary files for audit %s", audit_id)
