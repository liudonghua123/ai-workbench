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
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from sqlmodel import Session, select

from app.core import permissions as permission_catalog
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
from app.services import conventions as conventions_service
from app.services import deployments as deploy_service
from app.services import gitops, llm, scaffold, sql_guard
from app.services.scaffold import CreateProjectSpec
from app.services.serializers import serialize_deployment, serialize_project


def _project_dict(project: Project, session: Session) -> dict[str, Any]:
    return serialize_project(session, project)


def describe_workbench() -> dict[str, Any]:
    """Describe conventions, providers and current counts so an agent can plan safely."""
    from app.core import context

    principal = context.current_principal()
    with Session(engine) as session:
        # the count is part of what the model reasons about, so it must be scoped
        # like the list it will fetch next
        project_count = len(
            [
                p
                for p in session.exec(select(Project)).all()
                if principal is None
                or principal.reaches(owner_id=p.owner_id, org_id=p.org_id, project_id=p.id)
            ]
        )
    return {
        "conventions": {
            "workspace": "data/projects/<project-slug>",
            "bucket": "<project-slug>-assets",
            "database": "<project-slug with - replaced by _>",
            "domain": f"<project-slug>.{settings.domain_suffix}",
            "preview_domain": f"<project-slug>{settings.preview_suffix}.{settings.domain_suffix}",
            "auto_env": [
                "DATABASE_URL",
                "STORAGE_BUCKET",
                "STORAGE_ENDPOINT",
                "PUBLIC_URL",
                "PORT",
                "PREVIEW_URL",
                "DEV_PORT",
            ],
            "type_specs": ["fullstack", "frontend", "backend"],
            "override_file": conventions_service.MANIFEST_NAME,
            "workflow": [
                "check_project_conventions -> list_project_files/read_project_file",
                "write_project_file -> start_preview -> preview_status",
                "git_commit -> publish_project (commit + tag deploy/v<N> + deploy)",
            ],
        },
        "providers": {
            "storage": settings.storage_provider,
            "database": settings.database_provider,
            "domain": settings.domain_provider,
            "deploy": settings.deploy_provider,
            "deploy_mode": settings.deploy_mode,
            "preview_mode": settings.preview_mode,
        },
        "templates": [spec.key for spec in scaffold.templates().list()],
        "agent_harness": llm.effective_harness(),
        "sql_mode": settings.sql_mode,
        "access": {
            "auth_enabled": settings.auth_enabled,
            "model": "organization -> membership role -> permission set (+ project bindings)",
            "builtin_roles": [role.key for role in permission_catalog.BUILTIN_ROLES],
            "permissions": permission_catalog.ALL_PERMISSIONS,
            "tools_requiring_permission": TOOL_PERMISSIONS,
            "note": "Agent 继承调用者权限：describe_access 查看，check_permission 预检",
        },
        "sso": {
            "kinds": ["cas", "oauth2", "oidc", "wecom", "dingtalk", "mock"],
            "login_path": f"{settings.api_prefix}/auth/{{provider}}/start",
            "callback_path": f"{settings.api_prefix}/auth/{{provider}}/callback",
        },
        "project_count": project_count,
        "tools": sorted(TOOLS),
    }


def list_templates() -> list[dict[str, Any]]:
    """List the application templates available for scaffolding."""
    return [spec.to_public() for spec in scaffold.templates().list()]


def list_projects() -> list[dict[str, Any]]:
    """List the projects the caller may see, with resources, domains and last deploy.

    Scoped exactly like ``GET /projects``: an ``own`` scope only returns the rows
    this caller owns or was granted, so an agent never enumerates a colleague's
    work on the user's behalf.
    """
    from app.core import context

    principal = context.current_principal()
    with Session(engine) as session:
        projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
        visible = [
            p
            for p in projects
            if principal is None
            or principal.reaches(owner_id=p.owner_id, org_id=p.org_id, project_id=p.id)
        ]
        return [_project_dict(p, session) for p in visible]  # type: ignore[attr-defined]


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
    from app.core import context

    # an agent creates projects *as the caller*: the row is owned by (and belongs
    # to the org of) the human who asked, so their own scope still covers it later
    principal = context.current_principal()
    with Session(engine) as session:
        project = scaffold.create_project(
            session,
            CreateProjectSpec(
                name=name,
                template_key=template_key,
                description=description,
                deploy=deploy,
                owner_id=(principal.user_id or principal.email) if principal else "",
                org_id=principal.org_id if principal else "",
            ),
        )
        payload = _project_dict(project, session)
    if deploy:
        payload["deployment"] = deploy_project(payload["id"])
    return payload


def _launch(deployment_id: str) -> None:
    """Start a deployment worker from sync code (REST, MCP and the agent all land here)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(deploy_service.run_deployment(deployment_id))
    else:
        loop.create_task(deploy_service.run_deployment(deployment_id))


def _await(coro: Any) -> Any:
    """Finish a coroutine from sync code, whether or not a loop is already running.

    Tool functions are sync by design (they are exposed over MCP and called directly
    by the harness). When a loop is running — an MCP request or an agent step — the
    coroutine is run to completion in a worker thread instead of being dropped.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def deploy_project(project_id: str, environment: str = "production") -> dict[str, Any]:
    """Queue a deployment for a project; returns the deployment id to poll."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        deployment = deploy_service.create_deployment(session, project, environment)
        deployment_id = deployment.id
    _launch(deployment_id)
    return {"deployment_id": deployment_id, "status": DeploymentStatus.QUEUED}


def run_project(project_id: str) -> dict[str, Any]:
    """Run the project's production command from the current working tree."""
    return deploy_project(project_id, "production")


def start_preview(project_id: str) -> dict[str, Any]:
    """Start the debug environment on <slug>-dev.<suffix> (own port, hot reload)."""
    with Session(engine) as session:
        project = scaffold.ensure_preview(session, scaffold.get_project(session, project_id))
        deployment = deploy_service.create_preview(session, project)
        payload = {
            "deployment_id": deployment.id,
            "environment": deploy_service.PREVIEW,
            "url": project.preview_url,
            "fqdn": project.preview_fqdn,
            "port": project.dev_port,
            "status": deployment.status,
        }
    _launch(payload["deployment_id"])
    return payload


def stop_preview(project_id: str) -> dict[str, Any]:
    """Stop the debug environment (the port is released immediately)."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        latest = deploy_service.latest_deployment(session, project.id, deploy_service.PREVIEW)
        if latest is None:
            raise ValidationFailed("没有可停止的预览")
        result = _await(deploy_service.stop_deployment(session, latest))
        return {"stopped": True, "deployment_id": result.id, "status": str(result.status)}


def preview_status(project_id: str) -> dict[str, Any]:
    """Read the debug environment: url, port, status and the last 40 log lines."""
    with Session(engine) as session:
        project = scaffold.ensure_preview(session, scaffold.get_project(session, project_id))
        latest = deploy_service.latest_deployment(session, project.id, deploy_service.PREVIEW)
        payload = {
            "project_id": project.id,
            "environment": deploy_service.PREVIEW,
            "fqdn": project.preview_fqdn,
            "url": project.preview_url,
            "port": project.dev_port,
            "command": conventions_service.resolve(project).dev_command,
            "status": latest.status if latest else "stopped",
            "mode": latest.image if latest else "",
            "error": latest.error if latest else "",
            "log_tail": (
                "\n".join((latest.log or "").strip().splitlines()[-40:]) if latest else ""
            ),
        }
    return payload


def publish_project(project_id: str, message: str = "") -> dict[str, Any]:
    """Commit the workspace, tag deploy/v<N> and deploy that exact commit."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        result = deploy_service.publish(session, project, message)
        deployment_id = result["deployment"].id
        # "nothing to commit" still deploys HEAD, so read the sha from either shape
        commit = result["commit"] if isinstance(result["commit"], dict) else {}
        head = commit.get("head") if isinstance(commit.get("head"), dict) else {}
        payload = {
            "project_id": project.id,
            "version": result["version"],
            "commit": {
                "committed": commit.get("committed", False),
                "sha": commit.get("sha", "") or head.get("sha", ""),
                "short": commit.get("short", "") or head.get("short", ""),
            },
            "tag": result["tag"].get("tag", ""),
            "deployment_id": deployment_id,
        }
    _launch(deployment_id)
    return payload


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


def check_project_conventions(project_id: str) -> dict[str, Any]:
    """Resolve the project's type convention and grade the workspace against it.

    Returns the expected layout, the effective dev/build/run commands, any
    workbench.yaml override, and a pass/warn/fail checklist.
    """
    with Session(engine) as session:
        project = scaffold.ensure_preview(session, scaffold.get_project(session, project_id))
        return conventions_service.check(project)


def search_project(project_id: str, query: str, limit: int = 40) -> list[dict[str, Any]]:
    """Search the whole workspace for a string; returns path, line number and text."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return scaffold.search_text(project, query, limit=min(max(limit, 1), 200))


def _repo(project: Project):
    from pathlib import Path

    root = Path(project.repo_path)
    if not root.is_dir():
        raise ValidationFailed("项目工作区不存在")
    return root


def git_status(project_id: str) -> dict[str, Any]:
    """Read the working tree: branch, changed files with line counts, head, tags."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return gitops.status(_repo(project))


def git_log(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """List recent commits (sha, message, author, date, tags)."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return gitops.log(_repo(project), min(max(limit, 1), 100))


def git_diff(project_id: str, path: str = "", ref: str = "") -> dict[str, Any]:
    """Unified diff for one file (or the whole tree) against HEAD or a given ref."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return gitops.diff(_repo(project), path, ref)


def git_commit(project_id: str, message: str) -> dict[str, Any]:
    """Commit the whole working tree. Returns committed=false when there is nothing to do."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        result = gitops.commit(_repo(project), message)
        scaffold.log_event(
            session,
            "project.committed",
            f"提交 {result.get('short') or '无改动'}",
            project_id=project.id,
            sha=result.get("sha", ""),
        )
        return result


def git_restore_file(project_id: str, path: str, ref: str = "HEAD") -> dict[str, Any]:
    """Discard local changes to one file by restoring it from a ref."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        return gitops.restore(_repo(project), path, ref or "HEAD")


def git_revert_commit(project_id: str, sha: str) -> dict[str, Any]:
    """Revert a commit as a new commit (history stays append-only)."""
    with Session(engine) as session:
        project = scaffold.get_project(session, project_id)
        result = gitops.revert(_repo(project), sha)
        scaffold.log_event(
            session,
            "project.reverted",
            f"回滚提交 {sha[:8]}",
            project_id=project.id,
            sha=result.get("sha", ""),
        )
        return result


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


def describe_access() -> dict[str, Any]:
    """Who am I, which permissions do I hold, and what can I not do in this org."""
    from app.core import context, permissions as permission_catalog

    principal = context.current_principal()
    if principal is None:
        return {"authenticated": False, "note": "无请求上下文（离线调用）"}
    payload = principal.to_dict()
    payload["authenticated"] = not principal.anonymous
    payload["may"] = {
        perm: principal.has(perm)
        for perm in ("project:create", "project:delete", "code:write",
                     "deploy:run", "deploy:publish", "member:manage",
                     "role:manage", "sso:manage", "org:delete")
    }
    payload["catalog"] = permission_catalog.describe()
    return payload


def list_organizations() -> list[dict[str, Any]]:
    """Organizations the current caller can see, with their role in each."""
    from app.core import context
    from app.models import Membership, MemberStatus, Organization
    from app.services import identity

    principal = context.current_principal()
    with Session(engine) as session:
        if principal is not None and not principal.anonymous and not principal.is_platform_admin:
            rows = [session.get(Organization, ref.id) for ref in principal.orgs]
        else:
            rows = session.exec(select(Organization)).all()
        items: list[dict[str, Any]] = []
        for org in [row for row in rows if row is not None]:
            members = session.exec(
                select(Membership).where(
                    Membership.org_id == org.id, Membership.status == MemberStatus.ACTIVE
                )
            ).all()
            role = next(
                (ref.role_key for ref in (principal.orgs if principal else ()) if ref.id == org.id),
                "",
            )
            items.append(
                identity.serialize_org(org, role_key=role, counts={"members": len(members)})
            )
        return items


def list_org_members(org_id: str = "") -> list[dict[str, Any]]:
    """Members of an organization (defaults to the caller's active organization)."""
    from app.core import context
    from app.models import Membership, MemberStatus, Organization, User
    from app.services import identity

    principal = context.current_principal()
    target = org_id or (principal.org_id if principal else "")
    if not target:
        raise ValidationFailed("需要指定 org_id")
    with Session(engine) as session:
        if session.get(Organization, target) is None:
            raise NotFoundError(f"组织不存在: {target}")
        rows = session.exec(
            select(Membership).where(
                Membership.org_id == target, Membership.status == MemberStatus.ACTIVE
            )
        ).all()
        items: list[dict[str, Any]] = []
        for membership in rows:
            user = session.get(User, membership.user_id)
            if user is None:
                continue
            payload = identity.serialize_user(user, membership)
            items.append(
                {
                    "id": payload["id"],
                    "email": payload["email"],
                    "name": payload["name"],
                    "role_key": payload["role_key"],
                    "title": membership.title,
                    "status": payload["status"],
                }
            )
        return items


def list_sso_providers(org_id: str = "") -> dict[str, Any]:
    """Which identity sources exist, and which fields a new one still needs."""
    from app.core import context
    from app.models import IdentityProvider, Organization
    from app.providers import auth as auth_providers
    from app.services import identity

    principal = context.current_principal()
    target = org_id or (principal.org_id if principal else "")
    with Session(engine) as session:
        configured = []
        if target:
            rows = session.exec(
                select(IdentityProvider).where(IdentityProvider.org_id == target)
            ).all()
            configured = [identity.serialize_provider(row) for row in rows]
        return {
            "org_id": target,
            "configured": configured,
            "supported_kinds": [
                {
                    "kind": kind,
                    "label": schema["label"],
                    "required": [f["key"] for f in schema["fields"] if f.get("required")],
                    "secret_fields": [f["key"] for f in schema["secrets"]],
                }
                for kind, schema in auth_providers.SCHEMA.items()
            ],
        }


def check_permission(permission: str, project_id: str = "") -> dict[str, Any]:
    """Ask before acting: does the caller hold ``permission`` (optionally on one project)."""
    from app.core import context

    principal = context.current_principal()
    if principal is None:
        return {"permission": permission, "allowed": True, "note": "无请求上下文"}

    if project_id:
        # Answer the question the caller is really asking. A role may hold
        # ``code:write`` and still not reach this row; saying "allowed" here and
        # failing later with a 404 would send the model chasing a phantom bug.
        from app.core.errors import NotFoundError
        from app.core.security import require_project

        with Session(engine) as session:
            try:
                project_id = require_project(session, principal, project_id).id
            except NotFoundError:
                return {
                    "permission": permission,
                    "project_id": project_id,
                    "allowed": False,
                    "role": principal.role,
                    "reason": "项目不存在或不在你的数据范围内（数据范围 "
                    f"{principal.data_scope}）",
                }

    allowed = principal.has(permission, project_id or None)
    return {
        "permission": permission,
        "project_id": project_id,
        "allowed": allowed,
        "role": principal.role,
        "data_scope": principal.data_scope,
        "reason": "" if allowed else f"角色 {principal.role} 在组织内没有 {permission}",
    }


TOOLS: dict[str, Any] = {
    "describe_workbench": describe_workbench,
    "list_templates": list_templates,
    "list_projects": list_projects,
    "get_project": get_project,
    "create_project": create_project,
    # run & release
    "run_project": run_project,
    "deploy_project": deploy_project,
    "deployment_status": deployment_status,
    "publish_project": publish_project,
    # debug
    "start_preview": start_preview,
    "stop_preview": stop_preview,
    "preview_status": preview_status,
    # code
    "list_project_files": list_project_files,
    "read_project_file": read_project_file,
    "write_project_file": write_project_file,
    "delete_project_file": delete_project_file,
    "search_project": search_project,
    # version control
    "git_status": git_status,
    "git_log": git_log,
    "git_diff": git_diff,
    "git_commit": git_commit,
    "git_restore_file": git_restore_file,
    "git_revert_commit": git_revert_commit,
    # contract & resources
    "check_project_conventions": check_project_conventions,
    "list_env_vars": list_env_vars,
    "set_env_var": set_env_var,
    "query_database": query_database,
    "add_domain": add_domain,
    "list_agents": list_agents,
    # access control
    "describe_access": describe_access,
    "check_permission": check_permission,
    "list_organizations": list_organizations,
    "list_org_members": list_org_members,
    "list_sso_providers": list_sso_providers,
}


# --------------------------------------------------------------------------
# authorisation
# --------------------------------------------------------------------------

# Tool name -> permission. Anything not listed runs for any authenticated caller
# (read-only helpers). Keeping this next to the registry means a new mutating tool
# without a permission is caught by review, not by an incident.
TOOL_PERMISSIONS: dict[str, str] = {
    "create_project": "project:create",
    "publish_project": "deploy:publish",
    "run_project": "deploy:run",
    "deploy_project": "deploy:run",
    "start_preview": "deploy:run",
    "stop_preview": "deploy:run",
    "write_project_file": "code:write",
    "delete_project_file": "code:write",
    "git_commit": "code:write",
    "git_restore_file": "code:write",
    "git_revert_commit": "deploy:rollback",
    "set_env_var": "env:write",
    "query_database": "data:read",
    "add_domain": "domain:manage",
    "list_org_members": "member:read",
    "list_sso_providers": "sso:read",
}


def _guard(tool: str, project_id: str | None) -> None:
    """An agent may do exactly what the human who invoked it may do.

    Two gates, mirroring the REST layer: the functional permission
    (``TOOL_PERMISSIONS``) and the data scope (:meth:`Principal.reaches`). The
    project reference may be an id *or* a slug — the agent sees slugs in prompts
    and will use them — so it is resolved before either check.
    """
    from app.core import context
    from app.core.config import settings
    from app.core.errors import ForbiddenError
    from app.core.security import require_project

    principal = context.current_principal()
    if principal is None:
        if settings.auth_enabled:
            raise ForbiddenError("缺少调用者身份，拒绝执行")
        return

    if project_id:
        # the agent may pass a slug; resolve it, then apply the same data-scope
        # rule the REST layer uses (404 — never a hint that the row exists)
        with Session(engine) as session:
            project_id = require_project(session, principal, project_id).id

    needed = TOOL_PERMISSIONS.get(tool)
    if needed and not principal.has(needed, project_id):
        raise ForbiddenError(f"调用者角色 {principal.role} 缺少权限 {needed}（工具 {tool}）")


def _guarded(name: str, fn: Any) -> Any:
    import functools

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        project_id = kwargs.get("project_id")
        if not project_id:
            for value in args:
                if isinstance(value, str) and value.startswith("proj_"):
                    project_id = value
                    break
        _guard(name, project_id)
        return fn(*args, **kwargs)

    wrapper.__doc__ = fn.__doc__
    return wrapper


TOOLS = {name: _guarded(name, fn) for name, fn in TOOLS.items()}

__all__ = ["TOOLS", "TOOL_PERMISSIONS", *TOOLS]
