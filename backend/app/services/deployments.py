"""Deployment orchestration: DB rows, log streaming, provider delegation.

Three verbs, one table
----------------------
``preview``    debug build on ``<slug>-dev.<suffix>`` — its own port, its own process,
               hot reload. Never blocks production and vice versa.
``production`` plain run on ``<slug>.<suffix>`` from the current working tree.
``publish``    commit → tag ``deploy/v<N>`` → deploy *that* commit, recording the sha
               on the row. "What is live" is therefore a git object, not a folder.

Every run is a ``Deployment`` row, so log streaming, stop, history and the UI work
the same for all three.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from sqlmodel import Session, select

from app.core.db import engine
from app.core.errors import ConflictError, ValidationFailed
from app.core.ids import new_id, port_open
from app.models import Deployment, DeploymentStatus, EnvVar, Event, Project, utcnow
from app.providers import deploy_provider
from app.providers.base import DeploySpec
from app.services import conventions, gitops, scaffold

PREVIEW = "preview"
PRODUCTION = "production"

ACTIVE_STATUSES = (DeploymentStatus.QUEUED, DeploymentStatus.BUILDING, DeploymentStatus.DEPLOYING)

# One in-flight run per project *per environment*. The DB check handles the common
# case; this lock also covers a stop/retry racing a background task in-process.
_RUN_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _RUN_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _RUN_LOCKS[key] = lock
    return lock


def active_deployment(
    session: Session, project_id: str, environment: str | None = None
) -> Deployment | None:
    statement = (
        select(Deployment)
        .where(Deployment.project_id == project_id)
        .where(Deployment.status.in_(ACTIVE_STATUSES))  # type: ignore[attr-defined]
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    )
    if environment:
        statement = statement.where(Deployment.environment == environment)
    return session.exec(statement).first()


def latest_deployment(
    session: Session, project_id: str, environment: str | None = None
) -> Deployment | None:
    statement = (
        select(Deployment)
        .where(Deployment.project_id == project_id)
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    )
    if environment:
        statement = statement.where(Deployment.environment == environment)
    return session.exec(statement).first()


def reconcile_preview(session: Session, project: Project) -> None:
    """Downgrade a ``running`` preview that nothing is listening on.

    Preview hosts and dev servers live inside this process. Restart the control plane
    and the rows survive while the processes do not — so "running" would keep pointing
    the UI at a dead port. Reachability, not the row, is the source of truth here.
    Production is deliberately exempt: ``WB_DEPLOY_MODE=simulate`` never opens a port.
    """
    if not project.dev_port:
        return
    # note: ACTIVE_STATUSES excludes RUNNING, so query the row directly
    row = session.exec(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .where(Deployment.environment == PREVIEW)
        .where(Deployment.status == DeploymentStatus.RUNNING)
        .order_by(Deployment.started_at.desc())  # type: ignore[attr-defined]
    ).first()
    if row is None:
        return
    if port_open(project.dev_port):
        return
    row.status = DeploymentStatus.STOPPED
    row.error = "预览进程已不存在（控制面重启或进程退出），请重启预览"
    row.finished_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)


def _next_version(session: Session, project: Project, environment: str, prefix: str) -> str:
    count = len(
        session.exec(
            select(Deployment)
            .where(Deployment.project_id == project.id)
            .where(Deployment.environment == environment)
        ).all()
    )
    return f"{prefix}{count + 1}"


def _create(
    session: Session,
    project: Project,
    *,
    environment: str,
    version: str,
    url: str,
    commit_sha: str = "",
) -> Deployment:
    if not project.repo_path:
        raise ValidationFailed("项目没有工作目录，无法部署")
    running = active_deployment(session, project.id, environment)
    if running is not None:
        raise ConflictError(
            f"项目已有进行中的 {environment} 部署 {running.version}（{running.status}），请等待完成或先停止",
            details={"deployment_id": running.id, "status": running.status},
        )
    deployment = Deployment(
        id=new_id("dep"),
        project_id=project.id,
        environment=environment,
        status=DeploymentStatus.QUEUED,
        version=version,
        url=url,
        commit_sha=commit_sha,
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
            message=f"{project.name} {environment} 部署已排队 ({version})",
            payload={"deployment_id": deployment.id, "commit_sha": commit_sha},
        )
    )
    session.commit()
    return deployment


def create_deployment(
    session: Session, project: Project, environment: str = PRODUCTION
) -> Deployment:
    scaffold.ensure_preview(session, project)
    if environment == PREVIEW:
        return create_preview(session, project)
    head = gitops.head_commit(Path(project.repo_path)) if project.repo_path else None
    return _create(
        session,
        project,
        environment=PRODUCTION,
        version=_next_version(session, project, PRODUCTION, "v"),
        url=project.primary_url or conventions.production_url(project),
        commit_sha=(head or {}).get("sha", ""),
    )


def create_preview(session: Session, project: Project) -> Deployment:
    project = scaffold.ensure_preview(session, project)
    return _create(
        session,
        project,
        environment=PREVIEW,
        version=_next_version(session, project, PREVIEW, "dev-"),
        url=project.preview_url,
    )


def _env_for(session: Session, project: Project, deployment: Deployment) -> dict[str, str]:
    rows = session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all()
    env = {row.key: row.value for row in rows}
    env["ENVIRONMENT"] = deployment.environment
    if deployment.environment == PREVIEW:
        # the app must know it is the preview, or it will emit production links
        env["PORT"] = str(project.dev_port or 0)
        env["PUBLIC_URL"] = project.preview_url
        env["PREVIEW_URL"] = project.preview_url
        env["WB_PREVIEW"] = "1"
    return env


def _spec_for(project: Project, deployment: Deployment) -> DeploySpec:
    resolved = conventions.resolve(project)
    is_preview = deployment.environment == PREVIEW
    return DeploySpec(
        deployment_id=deployment.id,
        project_slug=project.slug,
        repo_path=Path(project.repo_path),
        runtime=project.runtime,
        run_command=resolved.dev_command if is_preview else resolved.run_command,
        build_command="" if is_preview else resolved.build_command,
        port=(project.dev_port or 0) if is_preview else (project.port or 0),
        environment=deployment.environment,
        url=deployment.url or project.primary_url,
        project_name=project.name,
        project_type=resolved.spec.type,
        health_path=resolved.health_path,
    )


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------


async def run_deployment(deployment_id: str) -> None:
    """Serialise runs per project+environment, then execute the deployment."""
    with Session(engine) as probe:
        row = probe.get(Deployment, deployment_id)
        project_id, environment = (row.project_id, row.environment) if row else ("", "")
    if not project_id:
        return
    async with _lock_for(f"{project_id}:{environment}"):
        await _run_deployment(deployment_id)


async def _stop_previous_previews(session: Session, project: Project, keep_id: str) -> None:
    """A preview owns one port; a new one must retire the old process first."""
    rows = session.exec(
        select(Deployment)
        .where(Deployment.project_id == project.id)
        .where(Deployment.environment == PREVIEW)
        .where(Deployment.status == DeploymentStatus.RUNNING)
    ).all()
    for row in rows:
        if row.id == keep_id:
            continue
        try:
            await deploy_provider().stop(_spec_for(project, row))
        except Exception:  # noqa: BLE001 - a stuck old process must not block the new one
            pass
        row.status = DeploymentStatus.STOPPED
        row.finished_at = utcnow()
        session.add(row)
    session.commit()


async def _run_deployment(deployment_id: str) -> None:
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

        if deployment.environment == PREVIEW:
            await _stop_previous_previews(session, project, deployment.id)

        buffer: list[str] = []
        last_flush = time.monotonic()
        started = utcnow()

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

        spec = _spec_for(project, deployment)
        spec.env = _env_for(session, project, deployment)

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
                deployment.image = str(result.detail.get("mode", ""))
            if deployment.environment == PRODUCTION:
                project.primary_url = deployment.url
            else:
                project.preview_url = deployment.url or project.preview_url
            project.updated_at = utcnow()
            session.add(project)
            message = f"{project.name} {deployment.environment} 部署成功 {deployment.version}"
            kind = "deployment.succeeded"
        else:
            deployment.status = DeploymentStatus.FAILED
            deployment.error = error
            message = f"{project.name} {deployment.environment} 部署失败: {error}"
            kind = "deployment.failed"

        session.add(deployment)
        session.add(
            Event(
                id=new_id("evt"),
                kind=kind,
                project_id=project.id,
                message=message,
                payload={
                    "deployment_id": deployment.id,
                    "environment": deployment.environment,
                    "commit_sha": deployment.commit_sha,
                    "elapsed_s": round((utcnow() - started).total_seconds(), 2),
                },
            )
        )
        session.commit()


async def stop_deployment(session: Session, deployment: Deployment) -> Deployment:
    project = session.get(Project, deployment.project_id)
    if project is None:
        raise ValidationFailed("项目不存在")
    spec = _spec_for(project, deployment)
    await deploy_provider().stop(spec)
    deployment.status = DeploymentStatus.STOPPED
    deployment.finished_at = utcnow()
    session.add(deployment)
    session.commit()
    session.refresh(deployment)
    return deployment


# ---------------------------------------------------------------------------
# publish — version control is part of the deploy contract
# ---------------------------------------------------------------------------


def publish(
    session: Session, project: Project, message: str = "", *, tag: bool = True
) -> dict:
    """Commit the working tree, tag it, then deploy that exact commit."""
    if not project.repo_path:
        raise ValidationFailed("项目没有工作目录，无法发布")
    root = Path(project.repo_path)
    if not root.is_dir():
        raise ValidationFailed("项目工作区不存在")

    commit_result = gitops.commit(root, message or f"publish: {project.name}")
    head = gitops.head_commit(root) or {}
    sha = head.get("sha", "")

    version = _next_version(session, project, PRODUCTION, "v")
    tag_name = f"deploy/{version}"
    tag_result: dict = {}
    if tag and sha:
        try:
            tag_result = gitops.tag(root, tag_name, message or f"release {version}")
        except Exception as exc:  # noqa: BLE001 - an existing tag must not block a release
            tag_result = {"error": str(exc), "tag": tag_name}

    deployment = _create(
        session,
        project,
        environment=PRODUCTION,
        version=version,
        url=project.primary_url,
        commit_sha=sha,
    )
    session.add(
        Event(
            id=new_id("evt"),
            kind="project.published",
            project_id=project.id,
            message=f"{project.name} 发布 {version}（{sha[:8] or 'no-commit'}）",
            payload={
                "deployment_id": deployment.id,
                "commit_sha": sha,
                "tag": tag_result.get("tag", ""),
            },
        )
    )
    session.commit()
    return {
        "project_id": project.id,
        "version": version,
        "commit": commit_result,
        "tag": tag_result,
        "deployment": deployment,
    }
