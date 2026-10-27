from fastapi import APIRouter

from app.api.v1.endpoints.audits import router as audits_router

api_router = APIRouter()
api_router.include_router(audits_router, prefix="/audits", tags=["audits"])
