"""Deployment orchestration: DB rows, log streaming, provider delegation."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.db import engine
from app.core.errors import ValidationFailed
from app.core.ids import new_id
from app.models import Deployment, DeploymentStatus, Event, EnvVar, Project, utcnow
from app.providers import deploy_provider
from app.providers.base import DeploySpec


def create_deployment(session: Session, project: Project, environment: str = "production") -> Deployment:
    if not project.repo_path:
        raise ValidationFailed("项目没有工作目录，无法部署")
    count = len(
        session.exec(select(Deployment).where(Deployment.project_id == project.id)).all()
    )
    deployment = Deployment(
        id=new_id("dep"),
        project_id=project.id,
        environment=environment,
        status=DeploymentStatus.QUEUED,
        version=f"v{count + 1}",
        url=project.primary_url,
        provider=deploy_provider().name,
    )
    session.add(deployment)
    session.commit()
    session.refresh(deployment)
    session.add(
        Event(
            id=new_id("evt"),
            kind="deployment.queued",
            project_id=project.id,
            message=f"{project.name} 部署已排队 ({deployment.version})",
            payload={"deployment_id": deployment.id},
        )
    )
    session.commit()
    return deployment


def _env_for(session: Session, project: Project) -> dict[str, str]:
    rows = session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all()
    return {row.key: row.value for row in rows}


async def run_deployment(deployment_id: str) -> None:
    """Background worker. Owns its session so it survives the request that queued it."""
    with Session(engine) as session:
        deployment = session.get(Deployment, deployment_id)
        if deployment is None:
            return
        project = session.get(Project, deployment.project_id)
        if project is None:
            return

        deployment.status = DeploymentStatus.BUILDING
        session.add(deployment)
        session.commit()

        buffer: list[str] = []
        last_flush = time.monotonic()
        started = datetime.now(timezone.utc)

        def flush(force: bool = False) -> None:
            nonlocal last_flush
            if not buffer:
                return
            if not force and time.monotonic() - last_flush < 0.75:
                return
            deployment.log = (deployment.log or "") + "\n".join(buffer) + "\n"
            buffer.clear()
            last_flush = time.monotonic()
            session.add(deployment)
            session.commit()

        async def sink(line: str) -> None:
            buffer.append(line)
            flush()

        spec = DeploySpec(
            deployment_id=deployment.id,
            project_slug=project.slug,
            repo_path=Path(project.repo_path),
            runtime=project.runtime,
            run_command=project.run_command,
            port=project.port or 0,
            environment=deployment.environment,
            url=deployment.url or project.primary_url,
            env=_env_for(session, project),
        )

        try:
            deployment.status = DeploymentStatus.DEPLOYING
            session.add(deployment)
            session.commit()
            result = await deploy_provider().deploy(spec, sink)
        except Exception as exc:  # noqa: BLE001
            result = None
            error = str(exc)
            await sink(f"[error] {error}")
        else:
            error = result.error

        flush(force=True)
        deployment.finished_at = utcnow()
        if result is not None and result.status == "running":
            deployment.status = DeploymentStatus.RUNNING
            deployment.url = result.url or deployment.url
            if result.detail:
                deployment.image = result.detail.get("mode", "")
            project.primary_url = deployment.url
            project.updated_at = utcnow()
            session.add(project)
            message = f"{project.name} 部署成功 {deployment.version}"
            kind = "deployment.succeeded"
        else:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = error
            message = f"{project.name} 部署失败: {error}"
            kind = "deployment.failed"

        session.add(deployment)
        session.add(
            Event(
                id=new_id("evt"),
                kind=kind,
                project_id=project.id,
                message=message,
                payload={"deployment_id": deployment.id, "elapsed_s": round((datetime.now(timezone.utc) - started).total_seconds(), 2)},
            )
        )
        session.commit()



async def stop_deployment(session: Session, deployment: Deployment) -> Deployment:
    project = session.get(Project, deployment.project_id)
    if project is None:
        raise ValidationFailed("项目不存在")
    spec = DeploySpec(
        deployment_id=deployment.id,
        project_slug=project.slug,
        repo_path=Path(project.repo_path),
        runtime=project.runtime,
        run_command=project.run_command,
        port=project.port or 0,
        url=deployment.url,
    )
    await deploy_provider().stop(spec)
    deployment.status = DeploymentStatus.STOPPED
    deployment.finished_at = utcnow()
    session.add(deployment)
    session.commit()
    session.refresh(deployment)
    return deployment
