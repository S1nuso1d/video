from urllib.parse import parse_qs, urlparse
import re

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
    "www.youtube-nocookie.com",
    "youtube-nocookie.com",
}


def extract_video_id(url: str) -> str:
    """Extract a canonical 11-character YouTube video ID from common URL forms."""
    parsed = urlparse(url.strip())
    if parsed.scheme.lower() != "https":
        raise ValueError("Используйте HTTPS-ссылку на публичное видео YouTube.")

    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in _ALLOWED_HOSTS:
        raise ValueError("Разрешены только ссылки youtube.com, youtu.be и youtube-nocookie.com.")

    video_id: str | None = None
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0] or None
    else:
        segments = [part for part in parsed.path.split("/") if part]
        if parsed.path.rstrip("/") == "/watch" or parsed.path == "/watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]
        elif len(segments) >= 2 and segments[0] in {"shorts", "embed", "live", "v"}:
            video_id = segments[1]
        elif len(segments) == 1 and segments[0] == "watch":
            video_id = parse_qs(parsed.query).get("v", [None])[0]

    if not video_id or not _VIDEO_ID_RE.fullmatch(video_id):
        raise ValueError("Не удалось определить корректный идентификатор видео YouTube.")
    return video_id


def normalize_youtube_url(url: str) -> str:
    """Normalize common YouTube links. Time fragments are intentionally dropped: audit entire video."""
    video_id = extract_video_id(url)
    return f"https://www.youtube.com/watch?v={video_id}"
