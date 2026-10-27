from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.logging_config import configure_logging

configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Database schema is managed by Alembic; the API does not mutate schema on startup.
    yield


app = FastAPI(
    title=settings.project_name,
    version="0.1.0",
    description=(
        "Предварительная проверка публичных YouTube-видео по SFT Stage 1. "
        "Результаты не заменяют ручную проверку."
    ),
    lifespan=lifespan,
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "service": "video-auditor"}


@app.get("/", include_in_schema=False)
def root() -> JSONResponse:
    return JSONResponse(
        {
            "message": "Video Auditor API is running",
            "docs": "/docs",
            "warning": "Анализ предварительный; сомнительные случаи требуют ручной проверки.",
        }
    )
