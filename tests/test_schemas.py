import pytest
from pydantic import ValidationError

from app.schemas.audit import AuditCreate, ContentType


def test_audit_create_normalizes_youtube_url() -> None:
    payload = AuditCreate(youtube_url="https://www.youtube.com/watch?v=PX453ESfDpk&t=41s")
    assert payload.youtube_url == "https://www.youtube.com/watch?v=PX453ESfDpk"
    assert payload.video_id == "PX453ESfDpk"
    assert payload.content_type == ContentType.UNKNOWN


def test_audit_create_rejects_non_youtube_url() -> None:
    with pytest.raises((ValidationError, ValueError)):
        AuditCreate(youtube_url="https://example.com/video")
