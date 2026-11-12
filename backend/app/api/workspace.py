"""Workspace routes: conventions, preview (debug), run, publish, version control.

These are the four verbs of the project page:

``GET  /projects/{id}/conventions``  the resolved spec + a pass/warn/fail report
``POST /projects/{id}/preview/start`` debug on ``<slug>-dev.<suffix>``
``POST /projects/{id}/run``           run production from the working tree
``POST /projects/{id}/publish``        commit → tag → deploy that commit
``/projects/{id}/git/*``              status, log, diff, commit, restore, revert

Every mutation here is also a tool in ``system_tools`` — the UI is not a privileged
client, it is one of three callers.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from sqlmodel import Session

from app.core.db import get_session
from app.core.errors import ValidationFailed
from app.core.security import Principal, can_access, get_principal
from app.schemas import GitCommitRequest, GitRefRequest, PublishRequest
from app.services import conventions, deployments as deploy_service, gitops, scaffold
from app.services.serializers import serialize_deployment

router = APIRouter(prefix="/projects/{project_id}", tags=["workspace"])


def _load(session: Session, project_id: str, principal: Principal):
    project = scaffold.get_project(session, project_id)
    if not can_access(principal, project.owner_id, project.org_id):
        from app.core.errors import NotFoundError

        raise NotFoundError(f"项目不存在: {project_id}")
    return project


def _root(project) -> Path:
    root = Path(project.repo_path)
    if not root.is_dir():
        raise ValidationFailed("项目工作区不存在")
    return root


# --- conventions -----------------------------------------------------------


@router.get("/conventions")
def project_conventions(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = scaffold.ensure_preview(session, _load(session, project_id, principal))
    return conventions.check(project)


@router.get("/conventions/specs")
def convention_specs():
    """The three type specs, used by the docs page and the create dialog."""
    return conventions.specs()


# --- preview (debug) -------------------------------------------------------


@router.get("/preview")
def preview_state(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = scaffold.ensure_preview(session, _load(session, project_id, principal))
    deploy_service.reconcile_preview(session, project)
    latest = deploy_service.latest_deployment(session, project.id, deploy_service.PREVIEW)
    return {
        "project_id": project.id,
        "environment": deploy_service.PREVIEW,
        "fqdn": project.preview_fqdn,
        "url": project.preview_url,
        "port": project.dev_port,
        "command": conventions.resolve(project).dev_command,
        "status": latest.status if latest else "stopped",
        "latest": serialize_deployment(latest, include_log=False) if latest else None,
    }


@router.post("/preview/start", status_code=202)
def start_preview(
    project_id: str,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = scaffold.ensure_preview(session, _load(session, project_id, principal))
    deployment = deploy_service.create_preview(session, project)
    background.add_task(deploy_service.run_deployment, deployment.id)
    return serialize_deployment(deployment)


@router.post("/preview/stop")
async def stop_preview(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = scaffold.ensure_preview(session, _load(session, project_id, principal))
    latest = deploy_service.latest_deployment(session, project.id, deploy_service.PREVIEW)
    if latest is None:
        raise ValidationFailed("没有可停止的预览")
    return serialize_deployment(await deploy_service.stop_deployment(session, latest))


# --- run / publish ---------------------------------------------------------


@router.post("/run", status_code=202)
def run_project(
    project_id: str,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    deployment = deploy_service.create_deployment(session, project, deploy_service.PRODUCTION)
    background.add_task(deploy_service.run_deployment, deployment.id)
    return serialize_deployment(deployment)


@router.post("/publish", status_code=202)
def publish_project(
    project_id: str,
    payload: PublishRequest,
    background: BackgroundTasks,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    result = deploy_service.publish(session, project, payload.message)
    text = result["tag"].get("tag") or result["version"]
    result["deployment"] = serialize_deployment(result["deployment"])
    background.add_task(deploy_service.run_deployment, result["deployment"]["id"])
    result["summary"] = (
        f"{'已提交并发布' if result['commit'].get('committed') else '工作区无改动，直接发布'} "
        f"{text}"
    )
    return result


# --- version control -------------------------------------------------------


@router.get("/git")
def git_state(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    return gitops.status(_root(project))


@router.get("/git/log")
def git_log(
    project_id: str,
    limit: int = Query(default=30, ge=1, le=200),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    return {"commits": gitops.log(_root(project), limit), "tags": gitops.tags(_root(project))}


@router.get("/git/diff")
def git_diff(
    project_id: str,
    path: str = "",
    ref: str = "",
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    return gitops.diff(_root(project), path, ref)


@router.get("/git/show")
def git_show(
    project_id: str,
    sha: str,
    path: str = "",
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    """The diff a single commit introduced."""
    project = _load(session, project_id, principal)
    return gitops.commit_diff(_root(project), sha, path)


@router.get("/git/file")
def git_file(
    project_id: str,
    path: str,
    ref: str = "HEAD",
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    return {"path": path, "ref": ref, "content": gitops.show(_root(project), ref, path)}


@router.post("/git/commit")
def git_commit(
    project_id: str,
    payload: GitCommitRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    result = gitops.commit(_root(project), payload.message, payload.paths or None)
    scaffold.log_event(
        session,
        "project.committed",
        f"提交 {result.get('short') or '无改动'}",
        project_id=project.id,
        sha=result.get("sha", ""),
    )
    return result


@router.post("/git/restore")
def git_restore(
    project_id: str,
    payload: GitRefRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    return gitops.restore(_root(project), payload.path, payload.ref or "HEAD")


@router.post("/git/revert")
def git_revert(
    project_id: str,
    payload: GitRefRequest,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    result = gitops.revert(_root(project), payload.ref)
    scaffold.log_event(
        session,
        "project.reverted",
        f"回滚提交 {payload.ref[:8]}",
        project_id=project.id,
        sha=result.get("sha", ""),
    )
    return result
