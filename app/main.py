from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging

configure_logging()

UI_FILE = Path(__file__).parent / "static" / "index.html"

app = FastAPI(
    title=settings.project_name,
    version="0.7.0",
    description=(
        "Инструмент командного анализа публичных YouTube-видео по критериям проекта. "
        "Результаты не заменяют ручную проверку."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type"],
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "video-auditor"}


@app.get("/", include_in_schema=False)
def root() -> FileResponse:
    """Serve the lightweight workspace UI; the API can also be integrated elsewhere."""
    return FileResponse(UI_FILE, media_type="text/html")
