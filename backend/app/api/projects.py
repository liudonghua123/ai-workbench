from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError
from app.core.security import Principal, can_access, get_principal
from app.models import DomainBinding, EnvVar, Project, ProjectStatus
from app.schemas import DomainCreate, EnvVarUpsert, FileWrite, ProjectCreate
from app.providers import domain_provider
from app.services import scaffold
from app.services.scaffold import CreateProjectSpec
from app.services.serializers import serialize_domain, serialize_env, serialize_project

router = APIRouter(prefix="/projects", tags=["projects"])


def _load(session: Session, project_id: str, principal: Principal):
    project = scaffold.get_project(session, project_id)
    if not can_access(principal, project.owner_id):
        # do not leak existence across owners
        raise NotFoundError(f"项目不存在: {project_id}")
    return project


@router.get("")
def list_projects(
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()  # type: ignore[attr-defined]
    visible = [p for p in projects if can_access(principal, p.owner_id)]
    return [serialize_project(session, p) for p in visible]


@router.post("", status_code=201)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = scaffold.create_project(
        session,
        CreateProjectSpec(
            name=payload.name,
            template_key=payload.template_key,
            description=payload.description,
            slug=payload.slug,
            type=payload.type,
            resources=payload.resources,
            variables=payload.variables,
            deploy=payload.deploy,
        ),
    )
    if not principal.anonymous:
        project.owner_id = principal.name
        session.add(project)
        session.commit()
        session.refresh(project)
    body = serialize_project(session, project)
    body["deploy_requested"] = payload.deploy
    return body


@router.get("/{project_id}")
def get_project(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    return serialize_project(session, _load(session, project_id, principal))


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    purge_files: bool = Query(default=False),
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    scaffold.delete_project(session, project, purge_files=purge_files)


@router.post("/{project_id}/archive")
def archive_project(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    project = _load(session, project_id, principal)
    project.status = ProjectStatus.ARCHIVED
    session.add(project)
    session.commit()
    session.refresh(project)
    return serialize_project(session, project)


# --- files -----------------------------------------------------------------


@router.get("/{project_id}/tree")
def project_tree(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    return {"root": project.repo_path, "nodes": scaffold.list_tree(project)}


@router.get("/{project_id}/file")
def read_project_file(project_id: str, path: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    return scaffold.read_file(project, path)


@router.put("/{project_id}/file")
def write_project_file(project_id: str, payload: FileWrite, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    return scaffold.write_file(project, payload.path, payload.content)


@router.delete("/{project_id}/file", status_code=204)
def delete_project_file(project_id: str, path: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    scaffold.delete_file(project, path)
    scaffold.log_event(
        session, "project.file_deleted", f"删除 {path}", project_id=project.id, path=path
    )


# --- env -------------------------------------------------------------------


@router.get("/{project_id}/env")
def list_env(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    rows = session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all()
    return [serialize_env(row) for row in rows]


@router.put("/{project_id}/env")
def upsert_env(project_id: str, payload: EnvVarUpsert, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    row = session.exec(
        select(EnvVar).where(EnvVar.project_id == project.id, EnvVar.key == payload.key)
    ).first()
    if row is None:
        row = EnvVar(
            id=scaffold.new_id("env"),
            project_id=project.id,
            key=payload.key,
            value=payload.value,
            is_secret=payload.is_secret,
        )
    else:
        row.value = payload.value
        row.is_secret = payload.is_secret
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_env(row)


# --- domains ---------------------------------------------------------------


@router.get("/{project_id}/domains")
def list_domains(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    rows = session.exec(select(DomainBinding).where(DomainBinding.project_id == project.id)).all()
    return [serialize_domain(row) for row in rows]


@router.post("/{project_id}/domains", status_code=201)
def add_domain(project_id: str, payload: DomainCreate, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    suffix = payload.suffix or settings.domain_suffix
    fqdn = payload.subdomain if "." in payload.subdomain else f"{payload.subdomain}.{suffix}"
    target = settings.domain_base_url or f"127.0.0.1:{project.port}"
    info = domain_provider().bind(fqdn, target=target, project_id=project.id)
    row = DomainBinding(
        id=scaffold.new_id("dom"),
        project_id=project.id,
        fqdn=fqdn,
        suffix=suffix,
        provider=info.provider,
        status=info.status,
        target=target,
        is_primary=False,
        auto_generated=False,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_domain(row)


@router.post("/{project_id}/domains/{domain_id}/verify")
def verify_domain(project_id: str, domain_id: str, session: Session = Depends(get_session)):
    row = session.get(DomainBinding, domain_id)
    if row is None or row.project_id != project_id:
        raise NotFoundError("域名绑定不存在")
    info = domain_provider().verify(row.fqdn)
    row.status = info.status
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_domain(row)


# --- ide -------------------------------------------------------------------


@router.get("/{project_id}/ide")
def ide_entry(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    template = settings.ide_url_template
    url = ""
    if template:
        url = template.replace("{path}", project.repo_path).replace("{project}", project.slug)
    return {
        "project_id": project.id,
        "repo_path": project.repo_path,
        "vscode_url": url,
        "available": bool(url),
        "fallback": "builtin-editor",
        "note": "" if url else "未配置 WB_IDE_URL_TEMPLATE，使用内置编辑器（Monaco）",
    }
