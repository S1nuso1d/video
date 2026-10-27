from app.schemas.audit import AuditReport, DefectSeverity, EvidenceConfidence, ReviewCoverage, Verdict


# Whole-video exclusions from the rubric should not be dismissed as a short-lived defect.
WHOLE_VIDEO_REJECTION_CATEGORIES = {
    "ai_generated_or_edited",
    "video_under_5_seconds",
    "excluded_content_category",
}


def enforce_conservative_policy(report: AuditReport) -> AuditReport:
    """Apply conservative guardrails after validating Gemini's structured response."""
    reasons = list(report.manual_review_reasons)
    force_manual = False

    if report.review_coverage != ReviewCoverage.ADEQUATE_FOR_PRELIMINARY_ASSESSMENT:
        force_manual = True
        reasons.append("Модель не подтвердила достаточное покрытие ролика для предварительной оценки.")

    if any(
        defect.confidence == EvidenceConfidence.LOW or defect.severity == DefectSeverity.UNCERTAIN
        for defect in report.detected_defects
    ):
        force_manual = True
        reasons.append("Есть дефекты с низкой уверенностью или неопределённой критичностью.")

    critical_defects = [
        defect for defect in report.detected_defects
        if defect.severity == DefectSeverity.CRITICAL
    ]

    if report.verdict == Verdict.CANDIDATE_REJECTED and not any(
        defect.severity == DefectSeverity.CRITICAL and defect.confidence == EvidenceConfidence.HIGH
        for defect in report.detected_defects
    ):
        force_manual = True
        reasons.append("Отклонение не подтверждено критичным дефектом с высокой уверенностью.")

    for defect in critical_defects:
        if defect.estimated_video_fraction is None:
            force_manual = True
            reasons.append(
                f"Не удалось уверенно оценить долю видео для дефекта «{defect.category.value}»; проверьте вручную."
            )

    whole_video_issue = any(
        defect.category.value in WHOLE_VIDEO_REJECTION_CATEGORIES
        and defect.severity == DefectSeverity.CRITICAL
        and defect.confidence == EvidenceConfidence.HIGH
        for defect in report.detected_defects
    )

    high_confidence_critical_over_threshold = any(
        defect.severity == DefectSeverity.CRITICAL
        and defect.confidence == EvidenceConfidence.HIGH
        and defect.estimated_video_fraction is not None
        and defect.estimated_video_fraction >= 0.20
        for defect in report.detected_defects
    )

    all_critical_defects_below_threshold = bool(critical_defects) and all(
        defect.estimated_video_fraction is not None and defect.estimated_video_fraction < 0.20
        for defect in critical_defects
    )

    if force_manual:
        report.verdict = Verdict.MANUAL_REVIEW_REQUIRED
        report.manual_review_reasons = list(dict.fromkeys(reasons))
        report.markup_comment = "Требуется ручная проверка. " + " ".join(report.manual_review_reasons[:2])
        return report

    if whole_video_issue or high_confidence_critical_over_threshold:
        report.verdict = Verdict.CANDIDATE_REJECTED
        descriptions = [
            defect.description for defect in report.detected_defects
            if defect.severity == DefectSeverity.CRITICAL
        ]
        report.markup_comment = "Битое. " + "; ".join(descriptions[:3])
        report.manual_review_reasons = list(dict.fromkeys(reasons))
        return report

    # The user's rubric says a video can be accepted when a defect takes less than 20%
    # of total runtime. Only apply this when the model supplied estimates for all critical issues.
    if all_critical_defects_below_threshold:
        report.verdict = Verdict.CANDIDATE_ACCEPTED
        descriptions = [defect.description for defect in critical_defects]
        report.markup_comment = (
            "Предварительно подходит: обнаруженные дефекты оценены как занимающие менее 20% "
            "длительности ролика. Проверьте указанные интервалы вручную. "
            + "; ".join(descriptions[:2])
        )[:1000]
    elif report.verdict == Verdict.CANDIDATE_REJECTED:
        descriptions = [
            defect.description for defect in report.detected_defects
            if defect.severity == DefectSeverity.CRITICAL
        ]
        report.markup_comment = "Битое. " + "; ".join(descriptions[:3]) if descriptions else report.markup_comment
    elif report.verdict == Verdict.CANDIDATE_ACCEPTED and not report.detected_defects:
        report.markup_comment = "Артефакты по результатам предварительной проверки не выявлены."

    report.manual_review_reasons = list(dict.fromkeys(reasons))
    return report
