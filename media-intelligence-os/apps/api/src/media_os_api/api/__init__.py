"""API router aggregation for Media Intelligence OS."""

from fastapi import APIRouter

from media_os_api.api.routes import (
    analytics,
    auth,
    brands,
    content,
    health,
    research,
    studio,
    system,
)

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(system.router)
api_v1_router.include_router(brands.router)
api_v1_router.include_router(research.router)
api_v1_router.include_router(content.router)
api_v1_router.include_router(analytics.router)
api_v1_router.include_router(studio.router)

__all__ = ["api_v1_router", "health"]
