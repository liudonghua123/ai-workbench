from __future__ import annotations

from fastapi import APIRouter

from app.api import auth, orgs, platform, projects, system, workspace
from app.api import registry as registry_api

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(orgs.router)
api_router.include_router(projects.router)
api_router.include_router(platform.router)
api_router.include_router(workspace.router)
api_router.include_router(registry_api.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
