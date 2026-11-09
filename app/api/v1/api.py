from fastapi import APIRouter

from app.api.v1.endpoints.audits import router as audits_router
from app.api.v1.endpoints.auth import router as auth_router
from app.api.v1.endpoints.reviews import router as reviews_router

api_router = APIRouter()
api_router.include_router(auth_router, prefix="/auth", tags=["authentication"])
api_router.include_router(audits_router, prefix="/audits", tags=["audits"])
api_router.include_router(reviews_router, prefix="/reviews", tags=["human review"])
