import json

import pytest

from app.calibration_cli import main as calibration_cli_main
from app.services.calibration import (
    CalibrationDataError,
    calibration_report_markdown,
    evaluate_calibration,
    load_calibration_dataset,
)


def _sample(
    sample_id,
    expert_verdict,
    predicted_verdict,
    expert_defects=None,
    predicted_defects=None,
):
    return {
        "sample_id": sample_id,
        "expert_verdict": expert_verdict,
        "prediction_verdict": predicted_verdict,
        "expert_defects": set(expert_defects or []),
        "predicted_defects": set(predicted_defects or []),
    }


def test_evaluate_calibration_counts_verdict_errors_and_defect_metrics():
    samples = [
        _sample(
            "false-accept",
            "candidate_rejected",
            "candidate_accepted",
            ["black_frame_segment"],
            [],
        ),
        _sample(
            "false-reject",
            "candidate_accepted",
            "candidate_rejected",
            [],
            ["blur_suspected"],
        ),
        _sample(
            "manual",
            "candidate_rejected",
            "manual_review_required",
            ["freeze_frame_suspected"],
            ["freeze_frame_suspected"],
        ),
        _sample(
            "correct",
            "candidate_accepted",
            "candidate_accepted",
            ["blur_suspected"],
            ["blur_suspected"],
        ),
    ]

    report = evaluate_calibration(samples)
    verdict = report["verdict"]

    assert report["sample_count"] == 4
    assert verdict["accuracy"] == 0.5
    assert verdict["false_acceptance"] == {
        "count": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert verdict["false_rejection"] == {
        "count": 1,
        "denominator": 2,
        "rate": 0.5,
    }
    assert verdict["manual_review_count"] == 1
    assert verdict["decision_coverage"] == 0.75
    assert report["defects"]["per_label"]["black_frame_segment"]["false_negative"] == 1
    assert report["defects"]["per_label"]["blur_suspected"]["false_positive"] == 1


def test_normalises_predictions_from_real_audit_response_sections():
    record = {
        "sample_id": "video-001",
        "expert": {
            "verdict": "candidate_rejected",
            "defects": ["black_frame_segment", "video_under_5_seconds"],
        },
        "prediction": {
            "status": "completed",
            "report": {
                "verdict": "candidate_rejected",
                "detected_defects": [
                    {"category": "video_under_5_seconds"},
                ],
            },
            "technical_findings": [{"code": "video_under_5_seconds"}],
            "visual_findings": [{"code": "black_frame_segment"}],
        },
    }

    report = evaluate_calibration([record])

    assert report["defects"]["per_label"]["black_frame_segment"]["true_positive"] == 1
    assert report["defects"]["per_label"]["video_under_5_seconds"]["true_positive"] == 1
    assert report["verdict"]["accuracy"] == 1.0


def test_confusion_matrix_contains_manual_review_as_a_separate_class():
    report = evaluate_calibration([
        _sample("a", "candidate_accepted", "manual_review_required")
    ])

    assert report["verdict"]["confusion_matrix"]["candidate_accepted"][
        "manual_review_required"
    ] == 1
    assert report["verdict"]["balanced_accuracy"] == 0.0


def test_zero_predictions_produce_undefined_precision_not_crash():
    report = evaluate_calibration([
        _sample("missed", "candidate_rejected", "manual_review_required", ["blur_suspected"])
    ])

    metrics = report["defects"]["per_label"]["blur_suspected"]
    assert metrics["precision"] is None
    assert metrics["recall"] == 0.0
    assert metrics["f1"] == 0.0


def test_markdown_report_contains_key_business_metrics():
    report = evaluate_calibration([
        _sample("a", "candidate_rejected", "candidate_accepted", ["artifact"], [])
    ])

    markdown = calibration_report_markdown(report)

    assert "False acceptance" in markdown
    assert "Macro F1" in markdown
    assert "Verdict confusion matrix" in markdown


def test_load_calibration_dataset_reports_line_numbers_and_duplicate_ids(tmp_path):
    dataset = tmp_path / "samples.jsonl"
    common = {
        "sample_id": "duplicate",
        "expert": {"verdict": "candidate_accepted", "defects": []},
        "prediction": {"verdict": "candidate_accepted", "detected_defects": []},
    }
    dataset.write_text(
        json.dumps(common) + chr(10) + json.dumps(common) + chr(10),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationDataError, match="line 2: duplicate sample_id"):
        load_calibration_dataset(dataset)


def test_load_calibration_dataset_accepts_blank_lines_and_real_api_report_shape(tmp_path):
    dataset = tmp_path / "samples.jsonl"
    record = {
        "sample_id": "one",
        "expert": {"verdict": "candidate_accepted", "defects": []},
        "prediction": {
            "id": "audit-123",
            "report": {"verdict": "candidate_accepted", "detected_defects": []},
            "technical_findings": [],
            "visual_findings": [],
        },
    }
    dataset.write_text(chr(10) + json.dumps(record) + chr(10), encoding="utf-8")

    loaded = load_calibration_dataset(dataset)

    assert len(loaded) == 1
    assert evaluate_calibration(loaded)["verdict"]["accuracy"] == 1.0


def test_invalid_verdict_is_rejected_with_line_number(tmp_path):
    dataset = tmp_path / "invalid.jsonl"
    dataset.write_text(
        json.dumps({
            "sample_id": "bad",
            "expert": {"verdict": "maybe", "defects": []},
            "prediction": {"verdict": "candidate_accepted"},
        }),
        encoding="utf-8",
    )

    with pytest.raises(CalibrationDataError, match="line 1: expert.verdict"):
        load_calibration_dataset(dataset)


def test_empty_dataset_is_rejected(tmp_path):
    dataset = tmp_path / "empty.jsonl"
    dataset.write_text(chr(10) + "  " + chr(10), encoding="utf-8")

    with pytest.raises(CalibrationDataError, match="no calibration samples"):
        load_calibration_dataset(dataset)
