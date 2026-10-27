import pytest

from app.services.youtube_urls import extract_video_id, normalize_youtube_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=PX453ESfDpk", "PX453ESfDpk"),
        ("https://youtu.be/PX453ESfDpk?t=41", "PX453ESfDpk"),
        ("https://youtube.com/shorts/PX453ESfDpk", "PX453ESfDpk"),
        ("https://www.youtube.com/embed/PX453ESfDpk", "PX453ESfDpk"),
        ("https://www.youtube.com/live/PX453ESfDpk", "PX453ESfDpk"),
    ],
)
def test_extract_video_id(url: str, expected: str) -> None:
    assert extract_video_id(url) == expected


def test_normalize_drops_timestamp_to_audit_whole_video() -> None:
    assert normalize_youtube_url("https://youtu.be/PX453ESfDpk?t=41") == (
        "https://www.youtube.com/watch?v=PX453ESfDpk"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://www.youtube.com/watch?v=PX453ESfDpk",
        "https://evil.example/watch?v=PX453ESfDpk",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/",
    ],
)
def test_reject_invalid_urls(url: str) -> None:
    with pytest.raises(ValueError):
        normalize_youtube_url(url)
