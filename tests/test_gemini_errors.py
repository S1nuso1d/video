from types import SimpleNamespace

import pytest

from app.services.gemini_auditor import describe_gemini_error


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (503, "HTTP 503"),
        (429, "HTTP 429"),
        (504, "HTTP 504"),
    ],
)
def test_transient_gemini_errors_have_clear_messages(code: int, expected: str) -> None:
    assert expected in describe_gemini_error(SimpleNamespace(code=code))


def test_unknown_gemini_error_does_not_expose_provider_details() -> None:
    exc = SimpleNamespace(code=403, message="sensitive internal provider details")
    result = describe_gemini_error(exc)
    assert "Проверь журнал Worker" in result
    assert "sensitive internal provider details" not in result
