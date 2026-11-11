"""System tools — the single implementation of "what an agent can do".

Every capability is defined once here as a plain function. It is then exposed three
ways, with zero duplicated logic:

1. REST  → ``app/api/*`` routers call the same services (for the UI).
2. MCP   → ``app/mcp/server.py`` registers each function as an MCP tool.
3. Agent → the harness receives a ``{name: callable}`` map built from this module.

That is why "agent can create and modify applications" needs no special-casing.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.errors import NotFoundError, ValidationFailed  # noqa: F401  (re-exported for MCP wrappers)
from app.core.ids import new_id
from app.models import (
    Agent,
    Deployment,
    DeploymentStatus,
    DomainBinding,
    EnvVar,
    Project,
    ProjectResource,
    ResourceKind,
)
from app.providers import database_provider, domain_provider
from app.services import deployments as deploy_service
from app.services import llm, scaffold, sql_guard
from app.services.scaffold import CreateProjectSpec
from app.services.serializers import serialize_project


def _project_dict(project: Project, session: Session) -> dict[str, Any]:
    return serialize_project(session, project)


def describe_workbench() -> dict[str, Any]:
    """Describe conventions, providers and current counts so an agent can plan safely."""
    with Session(engine) as session:
        project_count = len(session.exec(select(Project)).all())
    return {
        "conventions": {
            "workspace": "data/projects/<project-slug>",
            "bucket": "<project-slug>-assets",
            "database": "<project-slug with - replaced by _>",
            "domain": f"<project-slug>.{settings.domain_suffix}",
            "auto_env": ["DATABASE_URL", "STORAGE_BUCKET", "STORAGE_ENDPOINT", "PUBLIC_URL", "PORT"],
            "deploy": "deploy_project -> deployment_status (poll until running/failed)",
        },
        "providers": {
            "storage": settings.storage_provider,
            "database": settings.database_provider,
            "domain": settings.domain_provider,
            "deploy": settings.deploy_provider,
            "deploy_mode": settings.deploy_mode,
        },
        "templates": [spec.key for spec in scaffold.templates().list()],
        "agent_harness": llm.effective_harness(),
        "sql_mode": settings.sql_mode,
        "project_count": project_count,
        "tools": sorted(TOOLS),
    }


def list_templates() -> list[dict[str, Any]]:
    """List the application templates available for scaffolding."""
    return [spec.to_public() for spec in scaffold.templates().list()]


def list_projects() -> list[dict[str, Any]]:
    """List every project in the workbench with its resources, domains and last deploy."""
    with Session(engine) as session:
        projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
        return [_project_dict(p, session) for p in projects]  # type: ignore[attr-defined]


def get_project(project_id: str) -> dict[str, Any]:
    """Get one project by id or slug."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return _project_dict(project, session)


def create_project(
    name: str,
    template_key: str = "fullstack-fastapi-nextjs",
    description: str = "",
    deploy: bool = False,
) -> dict[str, Any]:
    """Create a new application from a template, provisioning storage, database and domain."""
    with Session(engine) as session:
        project = scaffold.create_project(
            session,
            CreateProjectSpec(
                name=name, template_key=template_key, description=description, deploy=deploy
            ),
        )
        payload = _project_dict(project, session)
    if deploy:
        payload["deployment"] = deploy_project(payload["id"])
    return payload


def deploy_project(project_id: str, environment: str = "production") -> dict[str, Any]:
    """Queue a deployment for a project; returns the deployment id to poll."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        deployment = deploy_service.create_deployment(session, project, environment)
        deployment_id = deployment.id
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(deploy_service.run_deployment(deployment_id))
    else:
        loop.create_task(deploy_service.run_deployment(deployment_id))
    return {"deployment_id": deployment_id, "status": DeploymentStatus.QUEUED}


def deployment_status(project_id: str) -> dict[str, Any]:
    """Read the newest deployment (status, url, log tail) for a project."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        rows = session.exec(
            select(Deployment)
            .where(Deployment.project_id == project.id)
            .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
        ).all()
    if not rows:
        return {"project_id": project_id, "deployments": []}
    latest = rows[0]
    return {
        "project_id": project_id,
        "latest": {
            "id": latest.id,
            "status": latest.status,
            "version": latest.version,
            "url": latest.url,
            "error": latest.error,
            "log_tail": "\n".join((latest.log or "").strip().splitlines()[-40:]),
        },
        "history": [
            {"id": r.id, "status": r.status, "version": r.version, "started_at": str(r.started_at)}
            for r in rows[:10]
        ],
    }


def list_project_files(project_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """List the file tree of a project."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return scaffold.list_tree(project)[:limit]


def read_project_file(project_id: str, path: str) -> dict[str, Any]:
    """Read a single file of a project."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return scaffold.read_file(project, path)


def write_project_file(project_id: str, path: str, content: str) -> dict[str, Any]:
    """Create or overwrite a file inside a project workspace."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        result = scaffold.write_file(project, path, content)
        scaffold.log_event(
            session, "project.file_written", f"写入 {path}", project_id=project.id, path=path
        )
        return result


def set_env_var(project_id: str, key: str, value: str, is_secret: bool = False) -> dict[str, Any]:
    """Create or update an environment variable for a project."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        existing = session.exec(
            select(EnvVar).where(EnvVar.project_id == project.id, EnvVar.key == key)
        ).first()
        if existing:
            existing.value = value
            existing.is_secret = is_secret
            session.add(existing)
        else:
            existing = EnvVar(
                id=new_id("env"),
                project_id=project.id,
                key=key,
                value=value,
                is_secret=is_secret,
            )
            session.add(existing)
        session.commit()
        return {"key": key, "project_id": project.id, "updated": True}


def delete_project_file(project_id: str, path: str) -> dict[str, Any]:
    """Delete one file inside a project workspace (empty parent directories are pruned)."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        result = scaffold.delete_file(project, path)
        scaffold.log_event(
            session, "project.file_deleted", f"删除 {path}", project_id=project.id, path=path
        )
        return result


def list_env_vars(project_id: str) -> list[dict[str, Any]]:
    """List a project's environment variables (secret values are masked)."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        rows = session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all()
        return [
            {
                "key": row.key,
                "value": "***" if row.is_secret else row.value,
                "is_secret": row.is_secret,
                "scope": row.scope,
            }
            for row in rows
        ]


def query_database(project_id: str, sql: str, limit: int = 50) -> dict[str, Any]:
    """Run a read-only SQL query against the project's database (SELECT / WITH / EXPLAIN)."""
    statement = sql_guard.check(sql)
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        resource = session.exec(
            select(ProjectResource).where(
                ProjectResource.project_id == project.id,
                ProjectResource.kind == ResourceKind.DATABASE,
            )
        ).first()
    if resource is None:
        raise ValidationFailed("项目没有数据库资源")
    rows = database_provider().execute(
        resource.name, statement, limit=min(max(limit, 1), settings.sql_row_limit)
    )
    return {"database": resource.name, "rows": rows, "row_count": len(rows)}


def add_domain(project_id: str, subdomain: str, suffix: str = "") -> dict[str, Any]:
    """Bind an extra domain to a project. Bare labels get the configured suffix appended."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        zone = suffix or settings.domain_suffix
        fqdn = subdomain if "." in subdomain else f"{subdomain}.{zone}"
        target = settings.domain_base_url or f"127.0.0.1:{project.port}"
        info = domain_provider().bind(fqdn, target=target, project_id=project.id)
        row = DomainBinding(
            id=new_id("dom"),
            project_id=project.id,
            fqdn=fqdn,
            suffix=zone,
            provider=info.provider,
            status=info.status,
            target=target,
            is_primary=False,
            auto_generated=False,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return {
            "id": row.id,
            "fqdn": row.fqdn,
            "status": row.status,
            "provider": row.provider,
            "target": row.target,
        }


def list_agents() -> list[dict[str, Any]]:
    """List configured agents with their bound MCP servers and skills."""
    with Session(engine) as session:
        agents = session.exec(select(Agent)).all()
        return [
            {
                "id": a.id,
                "key": a.key,
                "name": a.name,
                "model": a.model,
                "description": a.description,
                "mcp_server_ids": a.mcp_server_ids,
                "skill_ids": a.skill_ids,
                "permission_scope": a.permission_scope,
            }
            for a in agents
        ]


def run_agent(agent_key: str, prompt: str, project_id: str | None = None) -> dict[str, Any]:
    """Run an agent against a prompt. The agent can call every system tool above."""
    from app.services.agent_runtime import run

    return run(agent_key, prompt, project_id=project_id)


TOOLS: dict[str, Any] = {
    "describe_workbench": describe_workbench,
    "list_templates": list_templates,
    "list_projects": list_projects,
    "get_project": get_project,
    "create_project": create_project,
    "deploy_project": deploy_project,
    "deployment_status": deployment_status,
    "list_project_files": list_project_files,
    "read_project_file": read_project_file,
    "write_project_file": write_project_file,
    "delete_project_file": delete_project_file,
    "list_env_vars": list_env_vars,
    "set_env_var": set_env_var,
    "query_database": query_database,
    "add_domain": add_domain,
    "list_agents": list_agents,
}


__all__ = ["TOOLS", *TOOLS]
