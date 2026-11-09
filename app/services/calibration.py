"""Offline evaluation of Video Auditor output against expert-labelled samples.

The calibration dataset stores labels and audit output, not source videos. Each
JSONL row is an independent sample identified by a stable sample_id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


VALID_VERDICTS = (
    "candidate_accepted",
    "candidate_rejected",
    "manual_review_required",
)


class CalibrationDataError(ValueError):
    """The expert-labelled calibration dataset is invalid."""


def _require_mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CalibrationDataError(f"{field} must be a JSON object")
    return value


def _require_verdict(value: Any, field: str) -> str:
    if not isinstance(value, str) or value not in VALID_VERDICTS:
        allowed = ", ".join(VALID_VERDICTS)
        raise CalibrationDataError(f"{field} must be one of: {allowed}")
    return value


def _normalise_labels(value: Any, field: str) -> set[str]:
    if not isinstance(value, list):
        raise CalibrationDataError(f"{field} must be a JSON array of defect codes")
    labels: set[str] = set()
    for index, label in enumerate(value):
        if not isinstance(label, str) or not label.strip():
            raise CalibrationDataError(f"{field}[{index}] must be a non-empty string")
        labels.add(label.strip().lower())
    return labels


def _extract_prediction_verdict(prediction: Mapping[str, Any]) -> str:
    report = prediction.get("report")
    report = report if isinstance(report, Mapping) else {}
    value = prediction.get("verdict") or report.get("verdict")
    return _require_verdict(value, "prediction.verdict")


def _extract_prediction_defects(prediction: Mapping[str, Any]) -> set[str]:
    """Collect canonical codes from AI, FFprobe, and OpenCV result sections."""
    report = prediction.get("report")
    report = report if isinstance(report, Mapping) else {}

    detected = report.get("detected_defects")
    if not isinstance(detected, list):
        detected = prediction.get("detected_defects", [])

    labels: set[str] = set()
    for field, items in (
        ("prediction.detected_defects", detected),
        ("prediction.technical_findings", prediction.get("technical_findings", [])),
        ("prediction.visual_findings", prediction.get("visual_findings", [])),
    ):
        if items is None:
            continue
        if not isinstance(items, list):
            raise CalibrationDataError(f"{field} must be a JSON array")
        for index, item in enumerate(items):
            if not isinstance(item, Mapping):
                raise CalibrationDataError(f"{field}[{index}] must be a JSON object")
            code = item.get("category") or item.get("code")
            if not isinstance(code, str) or not code.strip():
                continue
            labels.add(code.strip().lower())
    return labels


def _ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 6)


def _mean(values: list[float | None]) -> float | None:
    defined = [value for value in values if value is not None]
    if not defined:
        return None
    return round(sum(defined) / len(defined), 6)


def _normalise_sample(raw: Any, line_number: int | None = None) -> dict[str, Any]:
    prefix = f"line {line_number}: " if line_number is not None else ""
    try:
        sample = _require_mapping(raw, "sample")
        sample_id = sample.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id.strip():
            raise CalibrationDataError("sample_id must be a non-empty string")

        expert = _require_mapping(sample.get("expert"), "expert")
        prediction = _require_mapping(sample.get("prediction"), "prediction")
        expert_verdict = _require_verdict(expert.get("verdict"), "expert.verdict")
        prediction_verdict = _extract_prediction_verdict(prediction)
        expert_defects = _normalise_labels(expert.get("defects", []), "expert.defects")
        predicted_defects = _extract_prediction_defects(prediction)

        return {
            "sample_id": sample_id.strip(),
            "expert_verdict": expert_verdict,
            "prediction_verdict": prediction_verdict,
            "expert_defects": expert_defects,
            "predicted_defects": predicted_defects,
        }
    except CalibrationDataError as exc:
        raise CalibrationDataError(prefix + str(exc)) from exc


def load_calibration_dataset(path: Path) -> list[dict[str, Any]]:
    """Load JSON Lines; blank lines are ignored and errors include a line number."""
    if not path.is_file():
        raise CalibrationDataError(f"Dataset file does not exist: {path}")

    samples: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    try:
        with path.open("r", encoding="utf-8-sig") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise CalibrationDataError(
                        f"line {line_number}: invalid JSON ({exc.msg})"
                    ) from exc
                sample = _normalise_sample(raw, line_number)
                if sample["sample_id"] in seen_ids:
                    raise CalibrationDataError(
                        f"line {line_number}: duplicate sample_id {sample['sample_id']!r}"
                    )
                seen_ids.add(sample["sample_id"])
                samples.append(sample)
    except OSError as exc:
        raise CalibrationDataError(f"Cannot read dataset file: {exc}") from exc

    if not samples:
        raise CalibrationDataError("Dataset contains no calibration samples")
    return samples


def evaluate_calibration(samples: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Calculate verdict and per-defect metrics for already-normalised samples."""
    rows = list(samples)
    if not rows:
        raise CalibrationDataError("Cannot evaluate an empty calibration dataset")

    confusion = {
        actual: {predicted: 0 for predicted in VALID_VERDICTS}
        for actual in VALID_VERDICTS
    }
    verdict_mismatches: list[dict[str, str]] = []
    defect_mismatches: list[dict[str, Any]] = []
    labels: set[str] = set()
    false_acceptances = 0
    false_rejections = 0
    manual_reviews = 0
    correct_verdicts = 0
    accepted_truth = 0
    rejected_truth = 0

    normalised_rows: list[dict[str, Any]] = []
    for raw in rows:
        if "expert_verdict" in raw and "prediction_verdict" in raw:
            row = dict(raw)
        else:
            row = _normalise_sample(raw)

        actual = _require_verdict(row.get("expert_verdict"), "expert_verdict")
        predicted = _require_verdict(row.get("prediction_verdict"), "prediction_verdict")
        sample_id = str(row.get("sample_id", "")).strip()
        if not sample_id:
            raise CalibrationDataError("sample_id must be a non-empty string")
        expert_defects = set(row.get("expert_defects", set()))
        predicted_defects = set(row.get("predicted_defects", set()))
        labels.update(expert_defects)
        labels.update(predicted_defects)

        confusion[actual][predicted] += 1
        correct_verdicts += int(actual == predicted)
        manual_reviews += int(predicted == "manual_review_required")
        accepted_truth += int(actual == "candidate_accepted")
        rejected_truth += int(actual == "candidate_rejected")
        false_acceptances += int(
            actual == "candidate_rejected" and predicted == "candidate_accepted"
        )
        false_rejections += int(
            actual == "candidate_accepted" and predicted == "candidate_rejected"
        )

        if actual != predicted:
            verdict_mismatches.append(
                {
                    "sample_id": sample_id,
                    "expert_verdict": actual,
                    "predicted_verdict": predicted,
                }
            )

        missing = sorted(expert_defects - predicted_defects)
        extra = sorted(predicted_defects - expert_defects)
        if missing or extra:
            defect_mismatches.append(
                {
                    "sample_id": sample_id,
                    "missed_defects": missing,
                    "extra_defects": extra,
                }
            )
        normalised_rows.append(
            {
                "sample_id": sample_id,
                "expert_verdict": actual,
                "prediction_verdict": predicted,
                "expert_defects": expert_defects,
                "predicted_defects": predicted_defects,
            }
        )

    per_defect: dict[str, dict[str, Any]] = {}
    precision_values: list[float | None] = []
    recall_values: list[float | None] = []
    f1_values: list[float | None] = []

    for label in sorted(labels):
        true_positive = false_positive = false_negative = true_negative = 0
        for row in normalised_rows:
            actual_positive = label in row["expert_defects"]
            predicted_positive = label in row["predicted_defects"]
            if actual_positive and predicted_positive:
                true_positive += 1
            elif not actual_positive and predicted_positive:
                false_positive += 1
            elif actual_positive and not predicted_positive:
                false_negative += 1
            else:
                true_negative += 1

        precision = _ratio(true_positive, true_positive + false_positive)
        recall = _ratio(true_positive, true_positive + false_negative)
        f1 = _ratio(
            2 * true_positive,
            2 * true_positive + false_positive + false_negative,
        )
        per_defect[label] = {
            "support": true_positive + false_negative,
            "predicted": true_positive + false_positive,
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "true_negative": true_negative,
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }
        precision_values.append(precision)
        recall_values.append(recall)
        f1_values.append(f1)

    per_verdict_recall: list[float | None] = []
    for verdict in VALID_VERDICTS:
        support = sum(confusion[verdict].values())
        correct = confusion[verdict][verdict]
        per_verdict_recall.append(_ratio(correct, support))

    sample_count = len(normalised_rows)
    decision_coverage = _ratio(sample_count - manual_reviews, sample_count)
    return {
        "schema_version": 1,
        "sample_count": sample_count,
        "verdict": {
            "labels": list(VALID_VERDICTS),
            "confusion_matrix": confusion,
            "accuracy": _ratio(correct_verdicts, sample_count),
            "balanced_accuracy": _mean(per_verdict_recall),
            "manual_review_count": manual_reviews,
            "manual_review_rate": _ratio(manual_reviews, sample_count),
            "decision_coverage": decision_coverage,
            "false_acceptance": {
                "count": false_acceptances,
                "denominator": rejected_truth,
                "rate": _ratio(false_acceptances, rejected_truth),
            },
            "false_rejection": {
                "count": false_rejections,
                "denominator": accepted_truth,
                "rate": _ratio(false_rejections, accepted_truth),
            },
            "mismatches": verdict_mismatches,
        },
        "defects": {
            "macro_precision": _mean(precision_values),
            "macro_recall": _mean(recall_values),
            "macro_f1": _mean(f1_values),
            "per_label": per_defect,
            "mismatches": defect_mismatches,
        },
        "limitations": [
            "Metrics are only meaningful when expert labels follow one stable rubric.",
            "A manual-review prediction is counted as neither a false acceptance nor a false rejection.",
            "Technical and visual finding codes are compared literally with expert defect codes.",
            "The dataset is a calibration sample, not proof of production-wide performance.",
        ],
    }


def _percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def calibration_report_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact human-readable Markdown report."""
    verdict = report["verdict"]
    defects = report["defects"]
    lines = [
        "# Video Auditor — Calibration report",
        "",
        f"- Samples: **{report['sample_count']}**",
        f"- Verdict accuracy: **{_percent(verdict['accuracy'])}**",
        f"- Balanced accuracy: **{_percent(verdict['balanced_accuracy'])}**",
        f"- Decision coverage: **{_percent(verdict['decision_coverage'])}**",
        f"- Manual review rate: **{_percent(verdict['manual_review_rate'])}**",
        f"- False acceptance: **{verdict['false_acceptance']['count']} / "
        f"{verdict['false_acceptance']['denominator']} "
        f"({_percent(verdict['false_acceptance']['rate'])})**",
        f"- False rejection: **{verdict['false_rejection']['count']} / "
        f"{verdict['false_rejection']['denominator']} "
        f"({_percent(verdict['false_rejection']['rate'])})**",
        "",
        "## Verdict confusion matrix",
        "",
        "| Expert / Predicted | Accepted | Rejected | Manual review |",
        "|---|---:|---:|---:|",
    ]
    short_names = {
        "candidate_accepted": "Accepted",
        "candidate_rejected": "Rejected",
        "manual_review_required": "Manual review",
    }
    matrix = verdict["confusion_matrix"]
    for actual in VALID_VERDICTS:
        counts = matrix[actual]
        lines.append(
            f"| {short_names[actual]} | "
            f"{counts['candidate_accepted']} | "
            f"{counts['candidate_rejected']} | "
            f"{counts['manual_review_required']} |"
        )

    lines.extend(
        [
            "",
            "## Defect detection",
            "",
            f"- Macro precision: **{_percent(defects['macro_precision'])}**",
            f"- Macro recall: **{_percent(defects['macro_recall'])}**",
            f"- Macro F1: **{_percent(defects['macro_f1'])}**",
            "",
            "| Defect code | Support | Predicted | Precision | Recall | F1 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    if defects["per_label"]:
        for label, metrics in defects["per_label"].items():
            lines.append(
                f"| `{label}` | {metrics['support']} | {metrics['predicted']} | "
                f"{_percent(metrics['precision'])} | {_percent(metrics['recall'])} | "
                f"{_percent(metrics['f1'])} |"
            )
    else:
        lines.append("| No defect labels | 0 | 0 | n/a | n/a | n/a |")

    lines.extend(["", "## Mismatches", ""])
    verdict_mismatches = verdict["mismatches"]
    defect_mismatches = defects["mismatches"]
    lines.append(f"- Verdict mismatches: **{len(verdict_mismatches)}**")
    lines.append(f"- Defect mismatches: **{len(defect_mismatches)}**")
    for item in verdict_mismatches:
        lines.append(
            f"  - `{item['sample_id']}`: expert `{item['expert_verdict']}`, "
            f"predicted `{item['predicted_verdict']}`"
        )
    for item in defect_mismatches:
        lines.append(
            f"  - `{item['sample_id']}`: missed={item['missed_defects']}, "
            f"extra={item['extra_defects']}"
        )

    lines.extend(["", "## Interpretation notes", ""])
    lines.extend(f"- {note}" for note in report["limitations"])
    lines.append("")
    return chr(10).join(lines)
