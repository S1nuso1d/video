from app.schemas.audit import (
    AuditReport,
    DefectCategory,
    DefectSeverity,
    DetectedDefect,
    EvidenceConfidence,
    ReviewCoverage,
    Verdict,
)
from app.services.report_policy import enforce_conservative_policy


def base_report(**kwargs) -> AuditReport:
    data = {
        "verdict": "candidate_accepted",
        "summary": "Явных проблем не обнаружено.",
        "review_coverage": "adequate_for_preliminary_assessment",
        "detected_defects": [],
        "not_assessed": [],
        "manual_review_reasons": [],
        "markup_comment": "Артефакты по результатам предварительной проверки не выявлены.",
    }
    data.update(kwargs)
    return AuditReport.model_validate(data)


def test_limited_coverage_requires_manual_review() -> None:
    report = enforce_conservative_policy(
        base_report(review_coverage=ReviewCoverage.LIMITED)
    )
    assert report.verdict == Verdict.MANUAL_REVIEW_REQUIRED
    assert report.manual_review_reasons


def test_rejection_without_high_confidence_critical_defect_requires_manual_review() -> None:
    report = base_report(verdict=Verdict.CANDIDATE_REJECTED)
    report = enforce_conservative_policy(report)
    assert report.verdict == Verdict.MANUAL_REVIEW_REQUIRED


def test_high_confidence_critical_defect_covering_at_least_20_percent_rejects() -> None:
    defect = DetectedDefect(
        category=DefectCategory.BLOCKINESS_PIXELATION_POSTERIZATION,
        severity=DefectSeverity.CRITICAL,
        start_time="00:10",
        end_time="01:10",
        estimated_video_fraction=0.25,
        confidence=EvidenceConfidence.HIGH,
        description="Крупная блочность заметна на основном объекте.",
    )
    report = base_report(detected_defects=[defect])
    report = enforce_conservative_policy(report)
    assert report.verdict == Verdict.CANDIDATE_REJECTED
    assert report.markup_comment.startswith("Битое.")


def test_unknown_fraction_for_critical_defect_requires_manual_review() -> None:
    defect = DetectedDefect(
        category=DefectCategory.DOUBLE_EXPOSURE,
        severity=DefectSeverity.CRITICAL,
        start_time="00:10",
        end_time="00:12",
        estimated_video_fraction=None,
        confidence=EvidenceConfidence.HIGH,
        description="При склейке заметно наложение двух кадров.",
    )
    report = enforce_conservative_policy(base_report(detected_defects=[defect]))
    assert report.verdict == Verdict.MANUAL_REVIEW_REQUIRED


def test_critical_defect_below_20_percent_can_be_accepted_by_rubric() -> None:
    defect = DetectedDefect(
        category=DefectCategory.DOUBLE_EXPOSURE,
        severity=DefectSeverity.CRITICAL,
        start_time="00:10",
        end_time="00:12",
        estimated_video_fraction=0.05,
        confidence=EvidenceConfidence.HIGH,
        description="Короткая двойная экспозиция на переходе.",
    )
    report = base_report(verdict=Verdict.CANDIDATE_REJECTED, detected_defects=[defect])
    report = enforce_conservative_policy(report)
    assert report.verdict == Verdict.CANDIDATE_ACCEPTED
    assert "менее 20%" in report.markup_comment
