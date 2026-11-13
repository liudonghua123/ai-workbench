from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api import api_router
from app.core import context as principal_context
from app.core import permissions as permission_catalog
from app.core import security
from app.core.config import settings
from app.core.db import init_db, session_scope
from app.core.errors import AppError, ForbiddenError
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
        "MCP servers, skills and agents — with organization-level RBAC and SSO "
        "(CAS / OAuth2 / OIDC / WeCom / DingTalk). The same API is exposed over MCP "
        f"at `{settings.mcp_path}`."
    ),
    lifespan=lifespan,
    docs_url="/docs",
    openapi_url="/openapi.json",
)


def _error_body(exc: AppError) -> dict:
    return {"error": {"code": exc.code, "message": exc.message, "details": exc.details}}


def _deny(request: Request, principal, permission: str, project_id: str | None) -> None:
    """Record the attempt before rejecting it — denials are the interesting rows."""
    try:
        with session_scope() as session:
            from app.services import audit

            audit.record(
                session,
                "access.denied",
                actor_id=principal.user_id,
                actor_name=principal.name,
                org_id=principal.org_id,
                resource_type=project_id or "route",
                resource_id=project_id or request.url.path,
                status="denied",
                detail={
                    "method": request.method,
                    "path": request.url.path,
                    "required": permission,
                    "role": principal.role,
                },
                ip=request.client.host if request.client else "",
            )
    except Exception:  # noqa: BLE001 - never let auditing break the rejection
        logger.warning("failed to audit denial", exc_info=True)


def _client_ip(request: Request) -> str:
    """Best-effort source address: honour one proxy hop, fall back to the socket."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "")


@app.middleware("http")
async def principal_middleware(request: Request, call_next):
    """Resolve identity once per request, then enforce the route's permission.

    Done in middleware rather than a dependency so the mounted MCP app at ``/mcp``
    is covered by the same policy as REST — one surface, one rule set.

    A valid token on a public path is still resolved (the login page needs to know
    who you already are); an *invalid* one is ignored there instead of turning the
    login page itself into a 401.
    """
    request.state.principal = security.ANONYMOUS_ADMIN
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    public = security.is_public(path)
    principal = security.ANONYMOUS_ADMIN

    if settings.auth_enabled:
        token = security.extract_token(request)
        try:
            if token or not public:
                principal = security.resolve(request)
            if not public:
                required, project_id = permission_catalog.permission_for(request.method, path)
                if required and not principal.has(required, project_id):
                    _deny(request, principal, required, project_id)
                    raise ForbiddenError(
                        f"缺少权限 {required}",
                        details={"required": required, "role": principal.role},
                    )
        except AppError as exc:
            if public and token and exc.status_code == 401:
                principal = security.ANONYMOUS_ADMIN
            else:
                headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else {}
                body = _error_body(exc)
                return JSONResponse(status_code=exc.status_code, content=body, headers=headers)

    request.state.principal = principal
    marker = principal_context.set_principal(principal)
    # the audit trail records who did what *from where*; handlers that have no
    # Request (services, agent tools) read this instead of re-parsing headers
    ip_marker = principal_context.set_client_ip(_client_ip(request))
    try:
        return await call_next(request)
    finally:
        principal_context.reset_client_ip(ip_marker)
        principal_context.reset_principal(marker)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins or ["*"],
    # Any loopback port while debugging: the console port is configurable, and a
    # CORS rejection is a terrible way to find out you moved it.
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content=_error_body(exc))


app.include_router(api_router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root():
    return {
        "app": settings.app_name,
        "version": __version__,
        "api": settings.api_prefix,
        "docs": "/docs",
        "mcp": settings.mcp_path,
        "auth": "enabled" if settings.auth_enabled else "disabled",
    }


# Mounted last so it only catches /mcp — REST routes win.
app.mount("/", mcp_app)
