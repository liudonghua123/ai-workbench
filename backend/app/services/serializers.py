"""Single source of truth for API payload shapes (used by REST and MCP)."""

from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.models import (
    Agent,
    AgentRun,
    Deployment,
    DomainBinding,
    EnvVar,
    McpServer,
    Project,
    ProjectResource,
    Skill,
    User,
)


def _owner(session: Session, owner_id: str) -> tuple[str, str]:
    """Display name and email for a project's owner (id, or legacy email/name)."""
    if not owner_id:
        return "", ""
    user = session.get(User, owner_id)
    if user is None:
        user = session.exec(select(User).where(User.email == owner_id)).first()
    if user is None:
        return owner_id, ""
    return user.name or user.email, user.email


def serialize_project(
    session: Session, project: Project, *, reconcile: bool = True
) -> dict[str, Any]:
    if reconcile:
        # imported here to keep serializers free of a module-level cycle
        from app.services.deployments import reconcile_preview

        reconcile_preview(session, project)
    resources = session.exec(
        select(ProjectResource).where(ProjectResource.project_id == project.id)
    ).all()
    domains = session.exec(
        select(DomainBinding).where(DomainBinding.project_id == project.id)
    ).all()
    deployments = session.exec(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    ).all()
    latest = deployments[0] if deployments else None
    preview_runs = [d for d in deployments if d.environment == "preview"]
    preview = preview_runs[0] if preview_runs else None
    production_runs = [d for d in deployments if d.environment == "production"]
    owner_name, owner_email = _owner(session, project.owner_id or "")
    return {
        "id": project.id,
        "slug": project.slug,
        "name": project.name,
        "description": project.description,
        "type": project.type,
        "status": project.status,
        "template_key": project.template_key,
        "template_version": project.template_version,
        "repo_path": project.repo_path,
        "runtime": project.runtime,
        "build_command": project.build_command,
        "run_command": project.run_command,
        "dev_command": project.dev_command,
        "dev_port": project.dev_port,
        "health_path": project.health_path,
        "port": project.port,
        "url": project.primary_url,
        "preview": {
            "fqdn": project.preview_fqdn,
            "url": project.preview_url,
            "port": project.dev_port,
            "command": project.dev_command,
            "status": preview.status if preview else "stopped",
            "deployment": serialize_deployment(preview) if preview else None,
            "last_deployed": serialize_deployment(production_runs[0]) if production_runs else None,
        },
        "git_initialized": project.git_initialized,
        "owner_id": project.owner_id or "",
        "owner_name": owner_name,
        "owner_email": owner_email,
        "org_id": project.org_id or "",
        "created_at": project.created_at.isoformat() if project.created_at else None,
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "meta": project.meta or {},
        "resources": [
            {
                "id": r.id,
                "kind": r.kind,
                "provider": r.provider,
                "name": r.name,
                "endpoint": r.endpoint,
                "status": r.status,
                "config": r.config,
            }
            for r in resources
        ],
        "domains": [serialize_domain(d) for d in domains],
        "deployment": serialize_deployment(latest) if latest else None,
        "deployment_count": len(deployments),
    }

def serialize_domain(domain: DomainBinding) -> dict[str, Any]:
    return {
        "id": domain.id,
        "fqdn": domain.fqdn,
        "suffix": domain.suffix,
        "provider": domain.provider,
        "status": domain.status,
        "target": domain.target,
        "is_primary": domain.is_primary,
        "auto_generated": domain.auto_generated,
        "url": f"{settings.gateway_scheme if settings.gateway_active else 'http'}://{domain.fqdn}",
    }


def serialize_deployment(deployment: Deployment, *, include_log: bool = False) -> dict[str, Any]:
    payload = {
        "id": deployment.id,
        "project_id": deployment.project_id,
        "environment": deployment.environment,
        "status": deployment.status,
        "version": deployment.version,
        "commit_sha": deployment.commit_sha,
        "image": deployment.image,
        "url": deployment.url,
        "provider": deployment.provider,
        "error": deployment.error,
        "started_at": deployment.started_at.isoformat() if deployment.started_at else None,
        "finished_at": deployment.finished_at.isoformat() if deployment.finished_at else None,
    }
    payload["log"] = deployment.log or "" if include_log else ""
    return payload


def serialize_env(env: EnvVar) -> dict[str, Any]:
    return {
        "id": env.id,
        "key": env.key,
        "value": "•" * 8 if env.is_secret else env.value,
        "is_secret": env.is_secret,
        "scope": env.scope,
    }


def serialize_mcp(server: McpServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "name": server.name,
        "description": server.description,
        "transport": server.transport,
        "endpoint": server.endpoint,
        "command": server.command,
        "args": server.args,
        "env": {
            k: ("***" if "KEY" in k.upper() or "TOKEN" in k.upper() else v)
            for k, v in (server.env or {}).items()
        },
        "status": server.status,
        "is_system": server.is_system,
        "tools": server.tools,
        "tool_count": len(server.tools or []),
        "last_checked_at": server.last_checked_at.isoformat() if server.last_checked_at else None,
    }


def serialize_skill(skill: Skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "key": skill.key,
        "name": skill.name,
        "version": skill.version,
        "description": skill.description,
        "source": skill.source,
        "source_path": skill.source_path,
        "enabled": skill.enabled,
        "manifest": skill.manifest,
        "tools": (skill.manifest or {}).get("tools", []),
    }


def serialize_agent(agent: Agent, session: Session | None = None) -> dict[str, Any]:
    payload = {
        "id": agent.id,
        "key": agent.key,
        "name": agent.name,
        "description": agent.description,
        "model": agent.model,
        "system_prompt": agent.system_prompt,
        "mcp_server_ids": agent.mcp_server_ids,
        "skill_ids": agent.skill_ids,
        "permission_scope": agent.permission_scope,
        "status": agent.status,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
    }
    if session is not None:
        servers = session.exec(select(McpServer)).all()
        skills = session.exec(select(Skill)).all()
        by_id = {s.id: s.name for s in servers}
        skill_by_id = {s.id: s.name for s in skills}
        payload["mcp_servers"] = [
            by_id.get(sid, sid) for sid in (agent.mcp_server_ids or [])
        ]
        payload["skills"] = [skill_by_id.get(sid, sid) for sid in (agent.skill_ids or [])]
    return payload


def serialize_run(run: AgentRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "agent_id": run.agent_id,
        "project_id": run.project_id,
        "prompt": run.prompt,
        "status": run.status,
        "output": run.output,
        "steps": run.steps,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }
