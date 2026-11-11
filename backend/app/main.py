from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import api_router
from app.core.config import settings
from app.core.db import init_db
from app.core.errors import AppError
from app.mcp.server import http_app as mcp_http_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("workbench")

mcp_app = mcp_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.seed import seed

    seed()
    logger.info("workbench ready: api=%s mcp=%s", settings.api_prefix, settings.mcp_path)
    # FastMCP's streamable-HTTP app needs its session manager started.
    async with mcp_app.lifespan(app):
        yield


app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description=(
        "Visual control plane for AI-built applications: scaffold, provision cloud "
        "resources, auto-bind a <project>.<suffix> domain, edit/deploy, and manage "
        "MCP servers, skills and agents. The same API is exposed over MCP at "
        f"`{settings.mcp_path}`."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message, "details": exc.details}},
    )


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root():
    return {
        "app": settings.app_name,
        "version": __version__,
        "api": settings.api_prefix,
        "docs": "/docs",
        "mcp": settings.mcp_path,
    }


# Mounted last so it only catches /mcp — REST routes win.
app.mount("/", mcp_app)
