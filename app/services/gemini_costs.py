"""Gemini usage normalization, transparent pricing estimates, and timing aggregation.

Costs are estimates based on the configured rate card, not provider invoices.
No cost is inferred from video duration when the provider omits token usage.
"""

from __future__ import annotations

from typing import Any, Mapping


def _read(value: Any, key: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(key)
    return getattr(value, key, None)


def _non_negative_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def normalise_interaction_usage(usage: Any) -> dict[str, int | None] | None:
    """Extract only documented token counters from an Interactions usage object."""
    if usage is None:
        return None

    result = {
        "input_tokens": _non_negative_int(_read(usage, "total_input_tokens")),
        "output_tokens": _non_negative_int(_read(usage, "total_output_tokens")),
        "thought_tokens": _non_negative_int(_read(usage, "total_thought_tokens")),
        "cached_input_tokens": _non_negative_int(_read(usage, "total_cached_tokens")),
        "total_tokens": _non_negative_int(_read(usage, "total_tokens")),
    }
    if result["input_tokens"] is None and result["output_tokens"] is None:
        return None
    return result


def _estimate_attempt_cost(
    usage: Mapping[str, int | None] | None,
    *,
    input_price_usd_per_million: float,
    cached_input_price_usd_per_million: float,
    output_price_usd_per_million: float,
) -> dict[str, float | None]:
    if usage is None:
        return {
            "input_cost_usd": None,
            "cached_input_cost_usd": None,
            "output_cost_usd": None,
            "estimated_cost_usd": None,
        }

    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if input_tokens is None or output_tokens is None:
        return {
            "input_cost_usd": None,
            "cached_input_cost_usd": None,
            "output_cost_usd": None,
            "estimated_cost_usd": None,
        }

    cached_tokens = usage.get("cached_input_tokens")
    cached_tokens = min(input_tokens, cached_tokens or 0)
    uncached_tokens = max(0, input_tokens - cached_tokens)

    input_cost = uncached_tokens * input_price_usd_per_million / 1_000_000
    cached_cost = cached_tokens * cached_input_price_usd_per_million / 1_000_000
    # Gemini's published output rate includes thinking tokens. Do not add
    # thought_tokens separately, or the estimate would double-count them.
    output_cost = output_tokens * output_price_usd_per_million / 1_000_000

    return {
        "input_cost_usd": round(input_cost, 10),
        "cached_input_cost_usd": round(cached_cost, 10),
        "output_cost_usd": round(output_cost, 10),
        "estimated_cost_usd": round(input_cost + cached_cost + output_cost, 10),
    }


def build_usage_attempt(
    usage: Mapping[str, int | None] | None,
    *,
    request_made: bool,
    attempt_number: int,
    model: str,
    pricing_version: str,
    input_price_usd_per_million: float,
    cached_input_price_usd_per_million: float,
    output_price_usd_per_million: float,
    usd_to_rub_rate: float,
) -> dict[str, Any] | None:
    """Create one immutable attempt record; a missing usage object stays unknown."""
    if not request_made:
        return None

    costs = _estimate_attempt_cost(
        usage,
        input_price_usd_per_million=input_price_usd_per_million,
        cached_input_price_usd_per_million=cached_input_price_usd_per_million,
        output_price_usd_per_million=output_price_usd_per_million,
    )
    estimate_usd = costs["estimated_cost_usd"]
    estimate_rub = (
        round(estimate_usd * usd_to_rub_rate, 6)
        if estimate_usd is not None and usd_to_rub_rate > 0
        else None
    )

    return {
        "attempt": attempt_number,
        "model": model,
        "pricing_version": pricing_version,
        "usage_available": usage is not None,
        "input_tokens": usage.get("input_tokens") if usage else None,
        "output_tokens": usage.get("output_tokens") if usage else None,
        "thought_tokens": usage.get("thought_tokens") if usage else None,
        "cached_input_tokens": usage.get("cached_input_tokens") if usage else None,
        "total_tokens": usage.get("total_tokens") if usage else None,
        **costs,
        "estimated_cost_rub": estimate_rub,
        "usd_to_rub_rate": usd_to_rub_rate if usd_to_rub_rate > 0 else None,
    }


def aggregate_usage(
    previous: Mapping[str, Any] | None,
    attempt: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Append an attempt and recalculate totals without inventing missing usage."""
    if attempt is None:
        return dict(previous) if previous else None

    existing_attempts = []
    if previous and isinstance(previous.get("attempts"), list):
        existing_attempts = [
            dict(item) for item in previous["attempts"] if isinstance(item, Mapping)
        ]

    # A fenced worker should write once per attempt. Replace a same-number
    # entry defensively instead of counting it twice after an application retry.
    attempt_number = attempt.get("attempt")
    existing_attempts = [
        item for item in existing_attempts if item.get("attempt") != attempt_number
    ]
    existing_attempts.append(dict(attempt))
    existing_attempts.sort(key=lambda item: int(item.get("attempt") or 0))

    def sum_counter(key: str) -> int | None:
        values = [item.get(key) for item in existing_attempts]
        known = [value for value in values if isinstance(value, int)]
        return sum(known) if known else None

    cost_keys = (
        "input_cost_usd",
        "cached_input_cost_usd",
        "output_cost_usd",
        "estimated_cost_usd",
        "estimated_cost_rub",
    )

    totals: dict[str, float | None] = {}
    for key in cost_keys:
        values = [item.get(key) for item in existing_attempts]
        if values and all(isinstance(value, (int, float)) for value in values):
            totals[key] = round(sum(float(value) for value in values), 10)
        else:
            totals[key] = None

    all_usage_available = all(
        bool(item.get("usage_available")) for item in existing_attempts
    )
    return {
        "model": attempt.get("model") or (previous or {}).get("model"),
        "pricing_version": attempt.get("pricing_version"),
        "request_count": len(existing_attempts),
        "usage_complete": all_usage_available,
        "input_tokens": sum_counter("input_tokens"),
        "output_tokens": sum_counter("output_tokens"),
        "thought_tokens": sum_counter("thought_tokens"),
        "cached_input_tokens": sum_counter("cached_input_tokens"),
        "total_tokens": sum_counter("total_tokens"),
        **totals,
        "usd_to_rub_rate": attempt.get("usd_to_rub_rate"),
        "attempts": existing_attempts,
    }


def aggregate_processing_metrics(
    previous: Mapping[str, Any] | None,
    *,
    attempt_number: int,
    stage_durations: Mapping[str, float],
    total_seconds: float,
) -> dict[str, Any]:
    """Store per-attempt durations and cumulative stage time for observability."""
    attempts = []
    if previous and isinstance(previous.get("attempts"), list):
        attempts = [
            dict(item) for item in previous["attempts"] if isinstance(item, Mapping)
            and item.get("attempt") != attempt_number
        ]

    stage_values = {
        str(name): round(max(0.0, float(seconds)), 3)
        for name, seconds in stage_durations.items()
        if isinstance(seconds, (int, float))
    }
    attempts.append({
        "attempt": attempt_number,
        "stage_durations_seconds": stage_values,
        "total_seconds": round(max(0.0, total_seconds), 3),
    })
    attempts.sort(key=lambda item: int(item.get("attempt") or 0))

    stage_totals: dict[str, float] = {}
    for item in attempts:
        stage_map = item.get("stage_durations_seconds") or {}
        if isinstance(stage_map, Mapping):
            for name, seconds in stage_map.items():
                if isinstance(seconds, (int, float)):
                    stage_totals[name] = stage_totals.get(name, 0.0) + float(seconds)

    return {
        "attempt_count": len(attempts),
        "total_seconds": round(
            sum(float(item.get("total_seconds") or 0.0) for item in attempts),
            3,
        ),
        "stage_totals_seconds": {
            name: round(seconds, 3) for name, seconds in stage_totals.items()
        },
        "last_attempt": attempts[-1] if attempts else None,
        "attempts": attempts,
    }
