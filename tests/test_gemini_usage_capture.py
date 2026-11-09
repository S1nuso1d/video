from types import SimpleNamespace

import pytest

from app.services import gemini_auditor


VALID_REPORT = """{
  "verdict": "candidate_accepted",
  "summary": "The video is provisionally suitable.",
  "review_coverage": "adequate_for_preliminary_assessment",
  "detected_defects": [],
  "not_assessed": [],
  "manual_review_reasons": [],
  "markup_comment": "Предварительно подходит."
}"""


def _client_for_interaction(interaction):
    return SimpleNamespace(
        interactions=SimpleNamespace(
            create=lambda **kwargs: interaction,
        ),
        files=SimpleNamespace(delete=lambda **kwargs: None),
    )


def test_successful_interaction_returns_normalised_usage(monkeypatch):
    monkeypatch.setattr(gemini_auditor.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(
        gemini_auditor,
        "_make_client",
        lambda: _client_for_interaction(SimpleNamespace(
            output_text=VALID_REPORT,
            usage=SimpleNamespace(
                total_input_tokens=1200,
                total_output_tokens=240,
                total_thought_tokens=80,
                total_cached_tokens=100,
                total_tokens=1440,
            ),
        )),
    )

    result = gemini_auditor.analyze_youtube_video(
        "https://www.youtube.com/watch?v=abcdefghijk",
        "live_action",
    )

    assert result.request_made is True
    assert result.report.verdict.value == "candidate_accepted"
    assert result.usage == {
        "input_tokens": 1200,
        "output_tokens": 240,
        "thought_tokens": 80,
        "cached_input_tokens": 100,
        "total_tokens": 1440,
    }


def test_usage_is_attached_to_error_when_report_json_is_invalid(monkeypatch):
    monkeypatch.setattr(gemini_auditor.settings, "gemini_api_key", "test-key")
    usage = SimpleNamespace(
        total_input_tokens=500,
        total_output_tokens=90,
        total_thought_tokens=20,
        total_cached_tokens=0,
        total_tokens=590,
    )
    monkeypatch.setattr(
        gemini_auditor,
        "_make_client",
        lambda: _client_for_interaction(SimpleNamespace(
            output_text="{ definitely not json",
            usage=usage,
        )),
    )

    with pytest.raises(gemini_auditor.GeminiAuditError) as captured:
        gemini_auditor.analyze_youtube_video(
            "https://www.youtube.com/watch?v=abcdefghijk",
            "live_action",
        )

    assert captured.value.request_made is True
    assert captured.value.usage["input_tokens"] == 500
    assert captured.value.usage["output_tokens"] == 90



def test_direct_youtube_uri_is_used_without_gemini_file_upload(monkeypatch):
    monkeypatch.setattr(gemini_auditor.settings, "gemini_api_key", "test-key")
    captured = {}

    class Interactions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text=VALID_REPORT, usage=None)

    class Files:
        def upload(self, **kwargs):
            raise AssertionError("Direct YouTube mode must not upload a local file")

        def delete(self, **kwargs):
            return None

    client = SimpleNamespace(interactions=Interactions(), files=Files())
    monkeypatch.setattr(gemini_auditor, "_make_client", lambda: client)

    result = gemini_auditor.analyze_youtube_video(
        "https://www.youtube.com/watch?v=abcdefghijk",
        "live_action",
    )

    assert result.report.verdict.value == "candidate_accepted"
    assert captured["input"][1] == {
        "type": "video",
        "uri": "https://www.youtube.com/watch?v=abcdefghijk",
    }
