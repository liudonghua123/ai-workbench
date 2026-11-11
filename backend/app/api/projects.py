from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import NotFoundError
from app.models import DomainBinding, EnvVar, Project, ProjectStatus
from app.schemas import DomainCreate, EnvVarUpsert, FileWrite, ProjectCreate
from app.providers import domain_provider
from app.services import scaffold
from app.services.scaffold import CreateProjectSpec
from app.services.serializers import serialize_domain, serialize_env, serialize_project

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("")
def list_projects(session: Session = Depends(get_session)):
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()  # type: ignore[attr-defined]
    return [serialize_project(session, p) for p in projects]


@router.post("", status_code=201)
def create_project(payload: ProjectCreate, session: Session = Depends(get_session)):
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
    body = serialize_project(session, project)
    body["deploy_requested"] = payload.deploy
    return body


@router.get("/{project_id}")
def get_project(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
    return serialize_project(session, project)


@router.delete("/{project_id}", status_code=204)
def delete_project(
    project_id: str,
    purge_files: bool = Query(default=False),
    session: Session = Depends(get_session),
):
    project = scaffold.get_project(session, project_id)
    scaffold.delete_project(session, project, purge_files=purge_files)


@router.post("/{project_id}/archive")
def archive_project(project_id: str, session: Session = Depends(get_session)):
    project = scaffold.get_project(session, project_id)
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
