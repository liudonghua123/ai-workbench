from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import get_session
from app.api.deps import visible_projects
from app.core.security import Principal, get_principal
from app.models import (
    Agent,
    AgentRun,
    Deployment,
    DeploymentStatus,
    Event,
    McpServer,
    Project,
    ProjectStatus,
    Skill,
)
from app.providers import registry
from app.services import llm, sql_guard

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


@router.get("/overview")
def overview(
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    # The dashboard is a view of the caller's data, not of the installation: an
    # own-scoped user must not learn the shape of everybody else's work from the
    # sidebar badge.
    projects = visible_projects(session, principal)
    ids = {p.id for p in projects}
    deployments = [
        d for d in session.exec(select(Deployment)).all() if d.project_id in ids
    ]
    agents = session.exec(select(Agent)).all()
    servers = session.exec(select(McpServer)).all()
    skills = session.exec(select(Skill)).all()
    # runs and events carry project ids, so they are filtered the same way: the
    # feed must not show "deployed customer-success" to someone who cannot see it
    runs = [
        r
        for r in session.exec(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(20)).all()  # type: ignore[attr-defined]
        if not r.project_id or r.project_id in ids
    ][:5]
    events = [
        e
        for e in session.exec(select(Event).order_by(Event.created_at.desc()).limit(40)).all()  # type: ignore[attr-defined]
        if not e.project_id or e.project_id in ids
    ][:12]

    running = [d for d in deployments if d.status == DeploymentStatus.RUNNING]
    failed = [d for d in deployments if d.status == DeploymentStatus.FAILED]

    return {
        "counts": {
            "projects": len(projects),
            "projects_ready": len([p for p in projects if p.status == ProjectStatus.READY]),
            "deployments": len(deployments),
            "deployments_running": len(running),
            "deployments_failed": len(failed),
            "agents": len(agents),
            "mcp_servers": len(servers),
            "skills": len(skills),
            "skills_enabled": len([s for s in skills if s.enabled]),
        },
        "domains": {
            "suffix": settings.domain_suffix,
            "base_url": settings.domain_base_url,
        },
        "providers": {
            "storage": settings.storage_provider,
            "database": settings.database_provider,
            "domain": settings.domain_provider,
            "deploy": settings.deploy_provider,
            "registered": {
                "storage": registry.available("storage"),
                "database": registry.available("database"),
                "domain": registry.available("domain"),
                "deploy": registry.available("deploy"),
            },
        },
        "deploy_mode": settings.deploy_mode,
        "mcp_endpoint": settings.mcp_path,
        "agent": {
            "harness": llm.effective_harness(),
            "model": llm.model_name(),
            "llm_configured": llm.configured(),
        },
        "auth": {"enabled": settings.auth_enabled},
        "sql": sql_guard.describe_mode(),
        "recent_events": [
            {
                "id": e.id,
                "kind": e.kind,
                "message": e.message,
                "project_id": e.project_id,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ],
        "recent_runs": [
            {
                "id": r.id,
                "prompt": r.prompt[:80],
                "status": r.status,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ],
    }


@router.get("/events")
def list_events(
    limit: int = Query(default=50, le=200),
    project_id: str | None = None,
    session: Session = Depends(get_session),
):
    stmt = select(Event).order_by(Event.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    if project_id:
        stmt = (
            select(Event)
            .where(Event.project_id == project_id)
            .order_by(Event.created_at.desc())  # type: ignore[attr-defined]
            .limit(limit)
        )
    rows = session.exec(stmt).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "message": row.message,
            "project_id": row.project_id,
            "payload": row.payload,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.get("/settings")
def public_settings():
    """Everything the UI needs to render env-specific affordances.

    Deliberately public and secret-free: the browser must be able to decide whether to
    show a token prompt *before* it holds a token.
    """
    return {
        "app_name": settings.app_name,
        "api_prefix": settings.api_prefix,
        "domain_suffix": settings.domain_suffix,
        "domain_base_url": settings.domain_base_url,
        "deploy_mode": settings.deploy_mode,
        "ide_url_template": settings.ide_url_template,
        "mcp_path": settings.mcp_path,
        "data_dir": settings.data_dir.as_posix(),
        "port_range": [settings.port_range_start, settings.port_range_end],
        "default_agent_model": settings.default_agent_model,
        "agent_harness": llm.effective_harness(),
        "llm_configured": llm.configured(),
        "auth_enabled": settings.auth_enabled,
        "sql": sql_guard.describe_mode(),
        "providers": {
            "storage": settings.storage_provider,
            "database": settings.database_provider,
            "domain": settings.domain_provider,
            "deploy": settings.deploy_provider,
        },
    }


# --- model / agent runtime -------------------------------------------------


@router.get("/llm")
def llm_status():
    return llm.status()


@router.post("/llm/test")
def llm_test():
    """One-shot connectivity check for the settings page."""
    return llm.probe()


@router.get("/me")
def whoami(principal: Principal = Depends(get_principal)):
    return {
        "principal": principal.to_dict(),
        "auth_enabled": settings.auth_enabled,
        "roles": {"viewer": "只读", "editor": "可写不可删", "admin": "全部"},
    }


@router.get("/harnesses")
def list_harnesses():
    """Registered agent harnesses — the extension point, surfaced for the UI."""
    from app.services.agent_runtime import get_harness

    active = llm.effective_harness()
    names = ["local", "llm"]
    return {
        "active": active,
        "items": [
            {
                "name": name,
                "available": True if name == "local" else llm.configured(),
                "active": name == active,
                "kind": get_harness(name).__class__.__name__,
            }
            for name in names
        ],
    }
