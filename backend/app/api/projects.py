from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.api.deps import load_scoped, scoped_project, visible_projects
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.core.security import Principal, get_principal
from app.models import DomainBinding, EnvVar, Project, ProjectStatus
from app.providers import domain_provider
from app.schemas import DomainCreate, EnvVarUpsert, FileWrite, ProjectCreate
from app.services import scaffold
from app.services.scaffold import CreateProjectSpec
from app.services.serializers import serialize_domain, serialize_env, serialize_project

router = APIRouter(prefix="/projects", tags=["projects"])


def _load(session: Session, project_id: str, principal: Principal):
    return load_scoped(session, project_id, principal)


@router.get("")
def list_projects(
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    projects = visible_projects(session, principal)
    return [serialize_project(session, p) for p in projects]


@router.post("", status_code=201)
def create_project(
    payload: ProjectCreate,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
):
    # the creator owns the row: that is what an "own scope" role keys off, so it
    # is always the stable user id — never a display name that can change
    owner_id = principal.user_id or principal.email or ""
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
            org_id=payload.org_id or principal.org_id,
            owner_id=owner_id,
        ),
    )
    if not principal.anonymous and project.owner_id != owner_id:
        project.owner_id = owner_id
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
def project_tree(project: Project = Depends(scoped_project)):
    return {"root": project.repo_path, "nodes": scaffold.list_tree(project)}


@router.get("/{project_id}/file")
def read_project_file(path: str, project: Project = Depends(scoped_project)):
    return scaffold.read_file(project, path)


@router.put("/{project_id}/file")
def write_project_file(payload: FileWrite, project: Project = Depends(scoped_project)):
    return scaffold.write_file(project, payload.path, payload.content)


@router.delete("/{project_id}/file", status_code=204)
def delete_project_file(
    path: str,
    project: Project = Depends(scoped_project),
    session: Session = Depends(get_session),
):
    scaffold.delete_file(project, path)
    scaffold.log_event(
        session, "project.file_deleted", f"删除 {path}", project_id=project.id, path=path
    )


# --- env -------------------------------------------------------------------


@router.get("/{project_id}/env")
def list_env(
    project: Project = Depends(scoped_project), session: Session = Depends(get_session)
):
    rows = session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all()
    return [serialize_env(row) for row in rows]


@router.put("/{project_id}/env")
def upsert_env(
    payload: EnvVarUpsert,
    project: Project = Depends(scoped_project),
    session: Session = Depends(get_session),
):
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
def list_domains(
    project: Project = Depends(scoped_project), session: Session = Depends(get_session)
):
    rows = session.exec(select(DomainBinding).where(DomainBinding.project_id == project.id)).all()
    return [serialize_domain(row) for row in rows]


@router.post("/{project_id}/domains", status_code=201)
def add_domain(
    payload: DomainCreate,
    project: Project = Depends(scoped_project),
    session: Session = Depends(get_session),
):
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
def ide_entry(project: Project = Depends(scoped_project)):
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
