from types import SimpleNamespace

from app.services.gemini_costs import (
    aggregate_processing_metrics,
    aggregate_usage,
    build_usage_attempt,
    normalise_interaction_usage,
)


def test_normalise_interaction_usage_supports_sdk_object_and_dict():
    sdk_usage = SimpleNamespace(
        total_input_tokens=1000,
        total_output_tokens=200,
        total_thought_tokens=50,
        total_cached_tokens=100,
        total_tokens=1200,
    )

    assert normalise_interaction_usage(sdk_usage) == {
        "input_tokens": 1000,
        "output_tokens": 200,
        "thought_tokens": 50,
        "cached_input_tokens": 100,
        "total_tokens": 1200,
    }
    assert normalise_interaction_usage({
        "total_input_tokens": 10,
        "total_output_tokens": 2,
    })["input_tokens"] == 10


def test_normalise_usage_returns_none_when_provider_did_not_report_counters():
    assert normalise_interaction_usage(None) is None
    assert normalise_interaction_usage(SimpleNamespace(status="completed")) is None


def test_cost_estimate_applies_uncached_cached_and_output_rates():
    usage = {
        "input_tokens": 1_000_000,
        "output_tokens": 100_000,
        "thought_tokens": 20_000,
        "cached_input_tokens": 200_000,
        "total_tokens": 1_100_000,
    }
    attempt = build_usage_attempt(
        usage,
        request_made=True,
        attempt_number=1,
        model="gemini-3.8-flash",
        pricing_version="test",
        input_price_usd_per_million=0.75,
        cached_input_price_usd_per_million=0.075,
        output_price_usd_per_million=3.75,
        usd_to_rub_rate=80,
    )

    assert attempt["input_cost_usd"] == 0.6
    assert attempt["cached_input_cost_usd"] == 0.015
    assert attempt["output_cost_usd"] == 0.375
    assert attempt["estimated_cost_usd"] == 0.99
    assert attempt["estimated_cost_rub"] == 79.2


def test_thought_tokens_are_not_double_counted_in_output_cost():
    attempt = build_usage_attempt(
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "thought_tokens": 20,
            "cached_input_tokens": 0,
            "total_tokens": 150,
        },
        request_made=True,
        attempt_number=1,
        model="gemini-3.8-flash",
        pricing_version="test",
        input_price_usd_per_million=1,
        cached_input_price_usd_per_million=0.1,
        output_price_usd_per_million=2,
        usd_to_rub_rate=0,
    )

    assert attempt["output_cost_usd"] == 0.0001
    assert attempt["estimated_cost_rub"] is None


def test_request_without_usage_is_recorded_but_not_given_invented_cost():
    attempt = build_usage_attempt(
        None,
        request_made=True,
        attempt_number=1,
        model="gemini-3.8-flash",
        pricing_version="test",
        input_price_usd_per_million=0.75,
        cached_input_price_usd_per_million=0.075,
        output_price_usd_per_million=3.75,
        usd_to_rub_rate=80,
    )

    result = aggregate_usage(None, attempt)

    assert result["request_count"] == 1
    assert result["usage_complete"] is False
    assert result["estimated_cost_usd"] is None
    assert result["estimated_cost_rub"] is None


def test_usage_aggregation_sums_attempts_without_double_counting_same_attempt():
    previous = aggregate_usage(None, {
        "attempt": 1,
        "model": "gemini-3.8-flash",
        "pricing_version": "test",
        "usage_available": True,
        "input_tokens": 100,
        "output_tokens": 20,
        "thought_tokens": 5,
        "cached_input_tokens": 0,
        "total_tokens": 120,
        "input_cost_usd": 0.000075,
        "cached_input_cost_usd": 0.0,
        "output_cost_usd": 0.000075,
        "estimated_cost_usd": 0.00015,
        "estimated_cost_rub": None,
        "usd_to_rub_rate": None,
    })
    second = dict(previous["attempts"][0])
    second.update({"attempt": 2, "input_tokens": 200, "estimated_cost_usd": 0.0003})
    result = aggregate_usage(previous, second)

    assert result["request_count"] == 2
    assert result["input_tokens"] == 300
    assert result["estimated_cost_usd"] == 0.00045
    assert len(result["attempts"]) == 2


def test_processing_metrics_aggregate_stages_and_preserve_attempt_breakdown():
    first = aggregate_processing_metrics(
        None,
        attempt_number=1,
        stage_durations={"download": 2.0, "gemini": 4.5},
        total_seconds=6.5,
    )
    result = aggregate_processing_metrics(
        first,
        attempt_number=2,
        stage_durations={"download": 1.0, "gemini": 3.0},
        total_seconds=4.0,
    )

    assert result["attempt_count"] == 2
    assert result["total_seconds"] == 10.5
    assert result["stage_totals_seconds"] == {"download": 3.0, "gemini": 7.5}
    assert result["last_attempt"]["attempt"] == 2
