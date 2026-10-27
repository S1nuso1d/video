from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging

configure_logging()

UI_FILE = Path(__file__).parent / "static" / "index.html"

app = FastAPI(
    title=settings.project_name,
    version="0.2.0",
    description=(
        "Предварительная проверка публичных YouTube-видео по SFT Stage 1. "
        "Результаты не заменяют ручную проверку."
    ),
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "video-auditor"}


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """Serve the lightweight web interface; APIs remain under /api/v1."""
    return FileResponse(UI_FILE, media_type="text/html")
