from __future__ import annotations

import json
import time
from typing import Iterator

from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine, get_session
from app.core.errors import NotFoundError, ValidationFailed
from app.core.security import Principal, can_access, get_principal
from app.models import Deployment, ProjectResource, ResourceKind
from app.providers import database_provider, deploy_provider, storage_provider, registry
from app.schemas import DeployRequest
from app.services import deployments as deploy_service
from app.services import scaffold, sql_guard
from app.services.serializers import serialize_deployment
from app.services.templates import build_registry

router = APIRouter(tags=["platform"])

TERMINAL_STATUSES = {"running", "failed", "stopped"}


def _project(session: Session, project_id: str, principal: Principal):
    project = scaffold.get_project(session, project_id)
    if not can_access(principal, project.owner_id, project.org_id, project.id):
        raise NotFoundError(f"项目不存在: {project_id}")
    return project


def _deployment(session: Session, deployment_id: str, principal: Principal):
    """A deployment is only readable through its project's data scope."""
    row = session.get(Deployment, deployment_id)
    if row is None:
        raise NotFoundError("部署不存在")
    project = scaffold.get_project(session, row.project_id)
    if not can_access(principal, project.owner_id, project.org_id, project.id):
        # same answer as "no such deployment": do not confirm other people's runs
        raise NotFoundError("部署不存在")
    return row


# --- templates -------------------------------------------------------------


@router.get("/templates")
def list_templates():
    return [spec.to_public() for spec in scaffold.templates().list()]


@router.get("/templates/{key}")
def get_template(key: str):
    spec = scaffold.templates().get(key)
    if spec is None:
        raise NotFoundError(f"模板不存在: {key}")
    return spec.to_public()


@router.post("/templates/reload")
def reload_templates():
    """Picks up new folders under data/templates without restarting the API."""
    scaffold._templates = build_registry([scaffold.settings.data_dir / "templates"])  # noqa: SLF001
    return {"count": len(scaffold.templates().list())}


# --- deployments -----------------------------------------------------------


@router.post("/projects/{project_id}/deployments", status_code=202)
def trigger_deploy(
    project_id: str,
    payload: DeployRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _project(session, project_id, principal)
    deployment = deploy_service.create_deployment(session, project, payload.environment)
    background.add_task(deploy_service.run_deployment, deployment.id)
    return serialize_deployment(deployment)


@router.get("/projects/{project_id}/deployments")
def list_deployments(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _project(session, project_id, principal)
    rows = session.exec(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [serialize_deployment(row) for row in rows]


@router.get("/deployments/{deployment_id}")
def get_deployment(
    deployment_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    row = _deployment(session, deployment_id, principal)
    return serialize_deployment(row, include_log=True)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


@router.get("/deployments/{deployment_id}/stream")
def stream_deployment(
    deployment_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> StreamingResponse:
    """Server-sent events: log deltas + status transitions until the run is terminal.

    Polling works, but a deploy that streams feels alive and saves the client from
    guessing an interval. Delta-based on log length so reconnects stay cheap.
    """

    _deployment(session, deployment_id, principal)

    def generate() -> Iterator[str]:
        sent_lines = 0
        last_status = ""
        deadline = time.monotonic() + 600
        while True:
            with Session(engine) as session:
                row = session.get(Deployment, deployment_id)
                if row is None:
                    yield _sse("error", {"message": "部署不存在"})
                    return
                lines = (row.log or "").splitlines()
                new_lines = lines[sent_lines:]
                sent_lines = len(lines)
                status = row.status
                snapshot = {
                    "id": row.id,
                    "status": status,
                    "version": row.version,
                    "url": row.url,
                    "error": row.error,
                    "log_lines": sent_lines,
                }
            if new_lines:
                yield _sse("log", {"lines": new_lines})
            if status != last_status:
                yield _sse("status", snapshot)
                last_status = status
            if status in TERMINAL_STATUSES:
                yield _sse("done", snapshot)
                return
            if time.monotonic() > deadline:
                yield _sse("timeout", {"message": "流式输出超时，请改用轮询"})
                return
            time.sleep(0.4)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/deployments/{deployment_id}/stop")
async def stop_deployment(
    deployment_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    row = await deploy_service.stop_deployment(session, _deployment(session, deployment_id, principal))
    return serialize_deployment(row)


# --- resources -------------------------------------------------------------


@router.get("/projects/{project_id}/resources")
def list_resources(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _project(session, project_id, principal)
    rows = session.exec(
        select(ProjectResource).where(ProjectResource.project_id == project.id)
    ).all()
    return [
        {
            "id": row.id,
            "kind": row.kind,
            "provider": row.provider,
            "name": row.name,
            "endpoint": row.endpoint,
            "status": row.status,
            "config": row.config,
        }
        for row in rows
    ]


class SqlQuery(BaseModel):
    sql: str
    limit: int = 100


@router.get("/system/sql-mode")
def sql_mode():
    return sql_guard.describe_mode()


@router.post("/projects/{project_id}/database/query")
def run_db_query(
    project_id: str,
    payload: SqlQuery,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _project(session, project_id, principal)
    # validate before touching the provider so a bad statement costs nothing
    statement = sql_guard.check(payload.sql)
    row = session.exec(
        select(ProjectResource).where(
            ProjectResource.project_id == project.id,
            ProjectResource.kind == ResourceKind.DATABASE,
        )
    ).first()
    if row is None:
        raise ValidationFailed("项目没有数据库资源")
    limit = min(max(payload.limit, 1), settings.sql_row_limit)
    rows = database_provider().execute(row.name, statement, limit=limit)
    return {
        "database": row.name,
        "provider": row.provider,
        "statement": statement,
        "mode": settings.sql_mode,
        "rows": rows,
        "row_count": len(rows),
    }


@router.get("/projects/{project_id}/storage/objects")
def list_objects(
    project_id: str,
    prefix: str = "",
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _project(session, project_id, principal)
    row = session.exec(
        select(ProjectResource).where(
            ProjectResource.project_id == project.id,
            ProjectResource.kind == ResourceKind.STORAGE,
        )
    ).first()
    if row is None:
        raise ValidationFailed("项目没有对象存储资源")
    objects = storage_provider().list_objects(row.name, prefix=prefix)
    return {"bucket": row.name, "objects": objects, "count": len(objects)}


# --- runtime introspection -------------------------------------------------


@router.get("/system/providers")
def list_providers():
    from app.core.config import settings

    return {
        "active": {
            "storage": settings.storage_provider,
            "database": settings.database_provider,
            "domain": settings.domain_provider,
            "deploy": settings.deploy_provider,
        },
        "available": {kind: registry.available(kind) for kind in ("storage", "database", "domain", "deploy")},
        "deploy_mode": deploy_provider().runtime_info(),
    }
