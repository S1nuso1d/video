"""Project criteria evaluation and conservative decision aggregation.

The engine is deliberately deterministic. It consumes existing FFprobe,
OpenCV, and Gemini evidence; it does not invent confidence scores or alter
the original Gemini verdict.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.schemas.criteria import CriteriaSpec


TECHNICAL_FIELD_READERS = {
    "duration_seconds": lambda metadata: metadata.get("duration_seconds"),
    "file_size_bytes": lambda metadata: metadata.get("file_size_bytes"),
    "width": lambda metadata: (metadata.get("video") or {}).get("width"),
    "height": lambda metadata: (metadata.get("video") or {}).get("height"),
    "frame_rate": lambda metadata: (metadata.get("video") or {}).get("frame_rate"),
    "has_audio": lambda metadata: metadata.get("has_audio"),
    "video_codec": lambda metadata: (metadata.get("video") or {}).get("codec"),
    "format_name": lambda metadata: metadata.get("format_name"),
    "video_bit_rate": lambda metadata: (metadata.get("video") or {}).get("bit_rate"),
    "container_bit_rate": lambda metadata: metadata.get("container_bit_rate"),
    "audio_stream_count": lambda metadata: len(metadata.get("audio_streams") or []),
}


def _compare(actual: Any, operator: str, expected: Any) -> bool:
    if operator == "eq":
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.casefold() == expected.casefold()
        return actual == expected
    if operator == "neq":
        if isinstance(actual, str) and isinstance(expected, str):
            return actual.casefold() != expected.casefold()
        return actual != expected
    if operator == "gt":
        return actual > expected
    if operator == "gte":
        return actual >= expected
    if operator == "lt":
        return actual < expected
    if operator == "lte":
        return actual <= expected
    if operator == "between":
        return expected[0] <= actual <= expected[1]
    if operator == "in":
        if isinstance(actual, str):
            return actual.casefold() in {
                str(item).casefold() for item in expected
            }
        return actual in expected
    if operator == "not_in":
        if isinstance(actual, str):
            return actual.casefold() not in {
                str(item).casefold() for item in expected
            }
        return actual not in expected
    raise ValueError(f"Unsupported criteria operator: {operator}")


def _rule_result(
    rule: Mapping[str, Any],
    *,
    status: str,
    observed: Any,
    reason: str,
) -> dict[str, Any]:
    return {
        "id": rule["id"],
        "type": rule["type"],
        "severity": rule["severity"],
        "status": status,
        "operator": rule["operator"],
        "field": rule.get("field"),
        "code": rule.get("code"),
        "expected": rule.get("value"),
        "observed": observed,
        "description": rule.get("description") or None,
        "reason": reason,
    }


def _evaluate_technical(
    rule: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if metadata is None:
        return _rule_result(
            rule,
            status="unknown",
            observed=None,
            reason="FFprobe metadata is unavailable; the rule cannot be checked.",
        )
    reader = TECHNICAL_FIELD_READERS[rule["field"]]
    actual = reader(metadata)
    if actual is None:
        return _rule_result(
            rule,
            status="unknown",
            observed=None,
            reason=f"Technical value {rule['field']} is missing from FFprobe metadata.",
        )
    try:
        passed = _compare(actual, rule["operator"], rule.get("value"))
    except (TypeError, ValueError, KeyError, IndexError):
        return _rule_result(
            rule,
            status="unknown",
            observed=actual,
            reason="The measured value cannot be compared with this rule.",
        )
    return _rule_result(
        rule,
        status="passed" if passed else "failed",
        observed=actual,
        reason=(
            "Measured technical value satisfies the project criterion."
            if passed
            else "Measured technical value violates the project criterion."
        ),
    )


def _evaluate_visual(
    rule: Mapping[str, Any],
    metadata: Mapping[str, Any] | None,
    findings: list[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    sampled = metadata.get("sampled_frames", 0) if isinstance(metadata, Mapping) else 0
    if not isinstance(sampled, int) or sampled <= 0:
        return _rule_result(
            rule,
            status="unknown",
            observed=None,
            reason="OpenCV did not provide a usable frame sample; visual coverage is unknown.",
        )

    matches = [
        item for item in (findings or [])
        if isinstance(item, Mapping) and item.get("code") == rule["code"]
    ]
    operator = rule["operator"]

    if operator == "finding_present":
        passed = bool(matches)
        observed: Any = len(matches)
        reason = (
            "The configured visual signal was found."
            if passed
            else "No matching visual signal was found in the sampled frames."
        )
    elif operator == "finding_absent":
        passed = not matches
        observed = len(matches)
        reason = (
            "No matching visual signal was found in the sampled frames."
            if passed
            else "The configured visual signal was found; inspect its evidence."
        )
    else:
        durations: list[float] = []
        missing_duration = False
        for finding in matches:
            evidence = finding.get("evidence")
            duration = evidence.get("estimated_duration_seconds") if isinstance(
                evidence, Mapping
            ) else None
            if isinstance(duration, (int, float)) and not isinstance(duration, bool):
                durations.append(float(duration))
            else:
                missing_duration = True
        if missing_duration:
            return _rule_result(
                rule,
                status="unknown",
                observed=durations or None,
                reason="A matching visual finding has no measured duration.",
            )
        actual = max(durations, default=0.0)
        passed = actual <= float(rule["value"])
        observed = actual
        reason = (
            "The longest matching visual interval is within the configured limit."
            if passed
            else "A matching visual interval exceeds the configured duration limit."
        )

    return _rule_result(
        rule,
        status="passed" if passed else "failed",
        observed=observed,
        reason=reason,
    )


def _evaluate_semantic(
    rule: Mapping[str, Any],
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        return _rule_result(
            rule,
            status="unknown",
            observed=None,
            reason="Gemini report is unavailable; semantic criteria were not evaluated.",
        )
    coverage = report.get("review_coverage")
    if coverage != "adequate_for_preliminary_assessment":
        return _rule_result(
            rule,
            status="unknown",
            observed=coverage,
            reason="Gemini reported limited or unavailable review coverage.",
        )

    defects = report.get("detected_defects")
    if not isinstance(defects, list):
        return _rule_result(
            rule,
            status="unknown",
            observed=None,
            reason="Gemini report has no valid detected_defects list.",
        )
    matches = [
        item for item in defects
        if isinstance(item, Mapping) and item.get("category") == rule["code"]
    ]
    uncertain_match = any(
        item.get("severity") == "uncertain" or item.get("confidence") == "low"
        for item in matches
    )
    if uncertain_match:
        return _rule_result(
            rule,
            status="unknown",
            observed=len(matches),
            reason="Gemini found only a low-confidence or uncertain matching defect.",
        )

    passed = bool(matches) if rule["operator"] == "defect_present" else not matches
    return _rule_result(
        rule,
        status="passed" if passed else "failed",
        observed=len(matches),
        reason=(
            "Gemini evidence satisfies the semantic criterion."
            if passed
            else "Gemini evidence violates the semantic criterion."
        ),
    )


def evaluate_criteria(
    criteria_spec: CriteriaSpec | Mapping[str, Any] | None,
    *,
    technical_metadata: Mapping[str, Any] | None,
    visual_metadata: Mapping[str, Any] | None,
    visual_findings: list[Mapping[str, Any]] | None,
    report: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Evaluate a project rubric. Return None when no rubric was attached."""
    if criteria_spec is None:
        return None
    spec = (
        criteria_spec
        if isinstance(criteria_spec, CriteriaSpec)
        else CriteriaSpec.model_validate(criteria_spec)
    )

    results: list[dict[str, Any]] = []
    for model in spec.rules:
        rule = model.model_dump(mode="json")
        if model.type == "technical":
            result = _evaluate_technical(rule, technical_metadata)
        elif model.type == "visual":
            result = _evaluate_visual(rule, visual_metadata, visual_findings)
        else:
            result = _evaluate_semantic(rule, report)
        results.append(result)

    passed_count = sum(item["status"] == "passed" for item in results)
    failed_count = sum(item["status"] == "failed" for item in results)
    unknown_count = sum(item["status"] == "unknown" for item in results)
    critical_failures = [
        item for item in results
        if item["status"] == "failed" and item["severity"] == "critical"
    ]

    if critical_failures:
        decision = "REJECT"
        names = ", ".join(item["id"] for item in critical_failures)
        summary = f"Отклонить: нарушены критические критерии — {names}."
    elif failed_count or unknown_count:
        decision = "REVIEW"
        summary = (
            "Нужна ручная проверка: есть невыполненные или непроверенные критерии "
            f"(нарушено: {failed_count}, неизвестно: {unknown_count})."
        )
    else:
        decision = "APPROVE"
        summary = f"Все критерии проекта «{spec.name}» выполнены ({passed_count})."

    return {
        "criteria_name": spec.name,
        "criteria_version": spec.version,
        "decision": decision,
        "summary": summary,
        "counts": {
            "total": len(results),
            "passed": passed_count,
            "failed": failed_count,
            "unknown": unknown_count,
        },
        "rules": results,
    }
