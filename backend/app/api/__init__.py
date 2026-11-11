from __future__ import annotations

from fastapi import APIRouter

from app.api import platform, projects, registry as registry_api, system, workspace

api_router = APIRouter()
api_router.include_router(projects.router)
api_router.include_router(platform.router)
api_router.include_router(workspace.router)
api_router.include_router(registry_api.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
