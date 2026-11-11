from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from sqlmodel import Session, select

from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationFailed
from app.models import Deployment, ProjectResource, ResourceKind
from app.providers import database_provider, deploy_provider, storage_provider, registry
from app.schemas import DeployRequest
from app.services import deployments as deploy_service
from app.services import scaffold
from app.services.serializers import serialize_deployment
from app.services.templates import build_registry

router = APIRouter(tags=["platform"])


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
):
    project = scaffold.get_project(session, project_id)
    deployment = deploy_service.create_deployment(session, project, payload.environment)
    background.add_task(deploy_service.run_deployment, deployment.id)
    return serialize_deployment(deployment)


@router.get("/projects/{project_id}/deployments")
def list_deployments(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    rows = session.exec(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [serialize_deployment(row) for row in rows]


@router.get("/deployments/{deployment_id}")
def get_deployment(deployment_id: str, session: Session = Depends(get_session)):
    row = session.get(Deployment, deployment_id)
    if row is None:
        raise NotFoundError("部署不存在")
    return serialize_deployment(row, include_log=True)


@router.post("/deployments/{deployment_id}/stop")
async def stop_deployment(deployment_id: str, session: Session = Depends(get_session)):
    row = session.get(Deployment, deployment_id)
    if row is None:
        raise NotFoundError("部署不存在")
    row = await deploy_service.stop_deployment(session, row)
    return serialize_deployment(row)


# --- resources -------------------------------------------------------------


@router.get("/projects/{project_id}/resources")
def list_resources(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
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


@router.post("/projects/{project_id}/database/query")
def run_db_query(project_id: str, payload: SqlQuery, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    row = session.exec(
        select(ProjectResource).where(
            ProjectResource.project_id == project.id,
            ProjectResource.kind == ResourceKind.DATABASE,
        )
    ).first()
    if row is None:
        raise ValidationFailed("项目没有数据库资源")
    if not payload.sql.strip():
        raise ValidationFailed("SQL 不能为空")
    rows = database_provider().execute(row.name, payload.sql, limit=payload.limit)
    return {"database": row.name, "provider": row.provider, "rows": rows, "row_count": len(rows)}


@router.get("/projects/{project_id}/storage/objects")
def list_objects(project_id: str, prefix: str = "", session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
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
