"""API router aggregation for Media Intelligence OS."""

from fastapi import APIRouter

from media_os_api.api.routes import auth, brands, health, system

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth.router)
api_v1_router.include_router(system.router)
api_v1_router.include_router(brands.router)

__all__ = ["api_v1_router", "health"]
