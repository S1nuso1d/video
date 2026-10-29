from types import SimpleNamespace

import pytest

from app.services.gemini_auditor import (
    GeminiAuditError,
    describe_gemini_error,
    is_retryable_gemini_error,
)


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


@pytest.mark.parametrize(
    ("exc", "expected"),
    [
        (TimeoutError("read timeout"), True),
        (ConnectionError("server disconnected"), True),
        (RuntimeError("permission denied"), False),
        (SimpleNamespace(code=503), True),
    ],
)
def test_transient_gemini_error_classification(exc: Exception, expected: bool) -> None:
    assert is_retryable_gemini_error(exc) is expected


def test_timeout_message_is_actionable() -> None:
    assert "повторён автоматически" in describe_gemini_error(TimeoutError("read timeout"))


def test_connection_message_is_safe() -> None:
    result = describe_gemini_error(ConnectionError("server disconnected with secret"))
    assert "Соединение с Gemini API прервалось" in result
    assert "secret" not in result


def test_gemini_audit_error_preserves_http_status_for_queue_cooldown() -> None:
    error = GeminiAuditError(
        "rate limit",
        retryable=True,
        status_code=429,
    )
    assert error.retryable is True
    assert error.status_code == 429
