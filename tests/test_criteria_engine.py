import pytest
from pydantic import ValidationError

from app.schemas.criteria import CriteriaSpec
from app.services.criteria_engine import evaluate_criteria


def _criteria(rules, name="Outdoor video project"):
    return CriteriaSpec.model_validate({"name": name, "rules": rules})


def _technical(field, operator, value, severity="critical", rule_id="technical_rule"):
    return {
        "id": rule_id,
        "type": "technical",
        "field": field,
        "operator": operator,
        "value": value,
        "severity": severity,
    }


def _visual(operator, code, value=None, severity="warning", rule_id="visual_rule"):
    rule = {
        "id": rule_id,
        "type": "visual",
        "operator": operator,
        "code": code,
        "severity": severity,
    }
    if value is not None:
        rule["value"] = value
    return rule


def _semantic(operator, code, severity="critical", rule_id="semantic_rule"):
    return {
        "id": rule_id,
        "type": "semantic",
        "operator": operator,
        "code": code,
        "severity": severity,
    }


def _evaluate(rules, *, technical=None, visual=None, findings=None, report=None):
    return evaluate_criteria(
        _criteria(rules),
        technical_metadata=technical,
        visual_metadata=visual,
        visual_findings=findings,
        report=report,
    )


def test_all_technical_rules_pass_and_approve():
    result = _evaluate(
        [
            _technical("height", "gte", 1080, rule_id="min_height"),
            _technical("has_audio", "eq", True, rule_id="audio"),
            _technical("duration_seconds", "between", [30, 180], rule_id="duration"),
        ],
        technical={
            "duration_seconds": 72.5,
            "has_audio": True,
            "video": {"height": 1080},
        },
    )

    assert result["decision"] == "APPROVE"
    assert result["counts"] == {"total": 3, "passed": 3, "failed": 0, "unknown": 0}


def test_critical_technical_failure_rejects():
    result = _evaluate(
        [_technical("height", "gte", 1080)],
        technical={"video": {"height": 720}},
    )

    assert result["decision"] == "REJECT"
    assert result["rules"][0]["status"] == "failed"
    assert result["rules"][0]["observed"] == 720


def test_warning_failure_requires_review_not_rejection():
    result = _evaluate(
        [_technical("has_audio", "eq", True, severity="warning")],
        technical={"has_audio": False},
    )

    assert result["decision"] == "REVIEW"
    assert result["counts"]["failed"] == 1


def test_missing_technical_metadata_is_unknown_and_requires_review():
    result = _evaluate(
        [_technical("height", "gte", 1080)],
        technical=None,
    )

    assert result["decision"] == "REVIEW"
    assert result["rules"][0]["status"] == "unknown"


def test_visual_max_duration_uses_timestamped_finding_evidence():
    rule = _visual("max_duration", "black_frame_segment", value=2.0)
    metadata = {"sampled_frames": 40}
    findings = [
        {
            "code": "black_frame_segment",
            "evidence": {"estimated_duration_seconds": 2.8},
        }
    ]

    result = _evaluate([rule], visual=metadata, findings=findings)

    assert result["decision"] == "REVIEW"
    assert result["rules"][0]["observed"] == 2.8
    assert result["rules"][0]["status"] == "failed"


def test_visual_signal_absence_passes_only_when_sampling_was_available():
    rule = _visual("finding_absent", "black_frame_segment")
    unavailable = _evaluate([rule], visual=None, findings=[])
    available = _evaluate(
        [rule],
        visual={"sampled_frames": 20},
        findings=[],
    )

    assert unavailable["rules"][0]["status"] == "unknown"
    assert available["rules"][0]["status"] == "passed"
    assert available["decision"] == "APPROVE"


def test_visual_present_rule_passes_when_signal_is_found():
    result = _evaluate(
        [_visual("finding_present", "blur_suspected")],
        visual={"sampled_frames": 20},
        findings=[{"code": "blur_suspected", "evidence": {}}],
    )

    assert result["decision"] == "APPROVE"
    assert result["rules"][0]["observed"] == 1


def test_semantic_rule_requires_adequate_gemini_coverage():
    result = _evaluate(
        [_semantic("defect_absent", "watermark_or_overlay")],
        report={
            "review_coverage": "limited",
            "detected_defects": [],
        },
    )

    assert result["decision"] == "REVIEW"
    assert result["rules"][0]["status"] == "unknown"


def test_semantic_uncertain_match_does_not_auto_reject():
    result = _evaluate(
        [_semantic("defect_absent", "watermark_or_overlay")],
        report={
            "review_coverage": "adequate_for_preliminary_assessment",
            "detected_defects": [
                {
                    "category": "watermark_or_overlay",
                    "severity": "uncertain",
                    "confidence": "low",
                }
            ],
        },
    )

    assert result["decision"] == "REVIEW"
    assert result["rules"][0]["status"] == "unknown"


def test_semantic_high_confidence_match_fails_absence_rule():
    result = _evaluate(
        [_semantic("defect_absent", "watermark_or_overlay")],
        report={
            "review_coverage": "adequate_for_preliminary_assessment",
            "detected_defects": [
                {
                    "category": "watermark_or_overlay",
                    "severity": "critical",
                    "confidence": "high",
                }
            ],
        },
    )

    assert result["decision"] == "REJECT"


def test_info_failure_does_not_reject_but_still_requires_review():
    result = _evaluate(
        [_technical("frame_rate", "gte", 30, severity="info")],
        technical={"video": {"frame_rate": 24}},
    )

    assert result["decision"] == "REVIEW"


def test_technical_rule_schema_rejects_unknown_fields_and_duplicate_ids():
    with pytest.raises(ValidationError):
        _criteria([_technical("arbitrary.path", "gte", 1)])
    with pytest.raises(ValidationError):
        _criteria([
            _technical("height", "gte", 1080, rule_id="min_height"),
            _technical("width", "gte", 1920, rule_id="min_height"),
        ])


def test_between_requires_ordered_numeric_bounds():
    with pytest.raises(ValidationError):
        _criteria([_technical("duration_seconds", "between", [180, 30])])


def test_no_criteria_returns_no_project_decision():
    assert evaluate_criteria(
        None,
        technical_metadata=None,
        visual_metadata=None,
        visual_findings=None,
        report=None,
    ) is None

def test_visual_rules_cannot_be_configured_as_critical():
    with pytest.raises(ValidationError, match="OpenCV visual signals are advisory"):
        _criteria([
            _visual(
                "finding_present",
                "black_frame_segment",
                severity="critical",
            )
        ])


def test_between_is_rejected_for_boolean_technical_fields():
    with pytest.raises(ValidationError, match="between can only be used with numeric"):
        _criteria([_technical("has_audio", "between", [0, 1])])


def test_audit_create_accepts_a_valid_project_criteria_spec():
    from app.schemas.audit import AuditCreate

    payload = AuditCreate.model_validate({
        "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "content_type": "live_action",
        "criteria_spec": {
            "name": "1080p only",
            "rules": [
                _technical("height", "gte", 1080),
            ],
        },
    })

    assert payload.criteria_spec is not None
    assert payload.criteria_spec.rules[0].field == "height"


def test_audit_create_rejects_unsupported_criteria_fields():
    from app.schemas.audit import AuditCreate

    with pytest.raises(ValidationError):
        AuditCreate.model_validate({
            "youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "criteria_spec": {
                "rules": [
                    _technical("arbitrary_path", "gte", 1),
                ],
            },
        })
