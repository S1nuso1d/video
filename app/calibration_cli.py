"""Command-line entry point for offline calibration evaluation.

Usage:
    python -m app.calibration_cli --input examples/calibration/example.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.services.calibration import (
    CalibrationDataError,
    calibration_report_markdown,
    evaluate_calibration,
    load_calibration_dataset,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compare Video Auditor outputs with expert labels and calculate "
            "verdict and defect-detection metrics."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="JSONL file with one expert/prediction pair per sample",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("calibration-report.json"),
        help="JSON report path (default: calibration-report.json)",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=None,
        help="Optional Markdown report path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        samples = load_calibration_dataset(args.input)
        report = evaluate_calibration(samples)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + chr(10),
            encoding="utf-8",
        )
        markdown = calibration_report_markdown(report)
        if args.markdown:
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(markdown, encoding="utf-8")
        print(markdown)
        print(chr(10) + f"JSON report saved to: {args.output}")
        if args.markdown:
            print(f"Markdown report saved to: {args.markdown}")
        return 0
    except (CalibrationDataError, OSError) as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
