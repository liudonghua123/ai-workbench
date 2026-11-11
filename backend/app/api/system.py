from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import get_session
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

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health")
def health():
    return {"status": "ok", "app": settings.app_name, "version": "0.1.0"}


@router.get("/overview")
def overview(session: Session = Depends(get_session)):
    projects = session.exec(select(Project)).all()
    deployments = session.exec(select(Deployment)).all()
    agents = session.exec(select(Agent)).all()
    servers = session.exec(select(McpServer)).all()
    skills = session.exec(select(Skill)).all()
    runs = session.exec(select(AgentRun).order_by(AgentRun.created_at.desc()).limit(5)).all()  # type: ignore[attr-defined]
    events = session.exec(select(Event).order_by(Event.created_at.desc()).limit(12)).all()  # type: ignore[attr-defined]

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
    """Everything the UI needs to render env-specific affordances."""
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
    }
