"""Scaffolding + project lifecycle. This is the only place that writes to disk for a project."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session, select

from app.core.config import settings
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.ids import allocate_port, new_id, random_suffix, slugify
from app.models import (
    Project,
    ProjectResource,
    ProjectStatus,
    ProjectType,
    ResourceKind,
    DomainBinding,
    DomainStatus,
    EnvVar,
    Event,
    utcnow,
)
from app.providers import database_provider, domain_provider, storage_provider
from app.services.templates import TemplateRegistry, TemplateSpec, build_registry, render

_templates: TemplateRegistry | None = None


def templates() -> TemplateRegistry:
    global _templates
    if _templates is None:
        _templates = build_registry([settings.data_dir / "templates"])
    return _templates


@dataclass(slots=True)
class CreateProjectSpec:
    name: str
    template_key: str
    description: str = ""
    slug: str = ""
    type: str = ""
    resources: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    deploy: bool = False


def log_event(session: Session, kind: str, message: str, *, project_id: str | None = None, **payload):
    session.add(
        Event(
            id=new_id("evt"),
            kind=kind,
            project_id=project_id,
            message=message,
            payload=payload,
        )
    )
    session.commit()


def _unique_slug(session: Session, candidate: str) -> str:
    slug = candidate
    n = 1
    while session.exec(select(Project).where(Project.slug == slug)).first() is not None:
        n += 1
        slug = f"{candidate}-{n}"
        if n > 50:
            raise ConflictError("无法分配唯一的项目标识")
    return slug


def build_context(spec: CreateProjectSpec, template: TemplateSpec, slug: str, port: int) -> dict[str, str]:
    fqdn = f"{slug}.{settings.domain_suffix}"
    db_name = slug.replace("-", "_")
    ctx = {
        "project_name": spec.name,
        "slug": slug,
        "description": spec.description or template.description,
        "port": str(port),
        "fqdn": fqdn,
        "fqdn_url": f"http://{fqdn}:{port}" if not settings.domain_base_url else settings.domain_base_url,
        "api_base": f"http://{fqdn}:{port}" if not settings.domain_base_url else settings.domain_base_url,
        "storage_bucket": f"{slug}-assets",
        "db_name": db_name,
        "db_dsn": f"sqlite:///./data/{db_name}.db",
        "template_key": template.key,
        "template_version": template.version,
        "project_type": template.type,
        "dev_command": template.run_command or "see README",
        "year": str(datetime.now(timezone.utc).year),
    }
    ctx.update({k: str(v) for k, v in spec.variables.items() if v is not None})
    return ctx


def create_project(session: Session, spec: CreateProjectSpec) -> Project:
    template = templates().get(spec.template_key)
    if template is None:
        raise ValidationFailed(f"模板不存在: {spec.template_key}")
    if not spec.name.strip():
        raise ValidationFailed("项目名称不能为空")

    # pure CJK names produce no ASCII slug — fall back to a stable random handle
    candidate = slugify(spec.slug or spec.name) or f"app-{random_suffix(5)}"
    slug = _unique_slug(session, candidate)
    taken_ports = {p.port for p in session.exec(select(Project)).all() if p.port}
    port = allocate_port(taken_ports)
    context = build_context(spec, template, slug, port)

    repo_path = settings.projects_dir / slug
    if repo_path.exists():
        raise ConflictError(f"项目目录已存在: {repo_path}")

    project = Project(
        id=new_id("proj"),
        slug=slug,
        name=spec.name.strip(),
        description=spec.description or template.description,
        type=spec.type or template.type,
        template_key=template.key,
        template_version=template.version,
        status=ProjectStatus.CREATING,
        repo_path=repo_path.as_posix(),
        runtime=template.runtime,
        run_command=render(template.run_command, context),
        port=port,
        primary_url=context["fqdn_url"],
        meta={"variables": context},
    )
    session.add(project)
    session.commit()
    log_event(session, "project.creating", f"开始创建项目 {project.name}", project_id=project.id)

    try:
        _write_files(repo_path, template, context)
        project.meta = {**project.meta, "files": sorted(template.files)}
        _provision_resources(session, project, template, spec.resources or template.resources, context)
        _set_env(session, project, context)
        _bind_domain(session, project, context["fqdn"], port)
        _git_init(repo_path)
        project.git_initialized = (repo_path / ".git").exists()
    except Exception as exc:  # noqa: BLE001 - surface any provider/template failure to the UI
        rolled_back = _rollback(session, project, context)
        project.status = ProjectStatus.FAILED
        project.meta = {**project.meta, "error": str(exc), "rolled_back": rolled_back}
        project.updated_at = utcnow()
        session.add(project)
        session.commit()
        log_event(
            session,
            "project.failed",
            f"项目创建失败并已回滚: {exc}",
            project_id=project.id,
            rolled_back=rolled_back,
        )
        raise

    project.status = ProjectStatus.READY
    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)
    log_event(session, "project.ready", f"项目 {project.name} 已就绪", project_id=project.id)
    return project


def _rollback(session: Session, project: Project, context: dict[str, str]) -> list[str]:
    """Best-effort compensation for a half-created project.

    Provisioning spans four providers, so a failure at step 4 must not leave a bucket,
    a database or a DNS record behind. Every step is individually guarded: a rollback
    that itself throws is worse than a partial rollback.
    """
    actions: list[str] = []

    for resource in session.exec(
        select(ProjectResource).where(ProjectResource.project_id == project.id)
    ).all():
        try:
            if resource.kind == ResourceKind.STORAGE:
                storage_provider().delete_bucket(resource.name)
            elif resource.kind == ResourceKind.DATABASE:
                database_provider().drop_database(resource.name)
            actions.append(f"removed {resource.kind}:{resource.name}")
        except Exception as exc:  # noqa: BLE001
            actions.append(f"failed to remove {resource.kind}:{resource.name}: {exc}")
        session.delete(resource)

    for domain in session.exec(
        select(DomainBinding).where(DomainBinding.project_id == project.id)
    ).all():
        try:
            domain_provider().unbind(domain.fqdn)
            actions.append(f"unbound domain:{domain.fqdn}")
        except Exception as exc:  # noqa: BLE001
            actions.append(f"failed to unbind {domain.fqdn}: {exc}")
        session.delete(domain)

    for env in session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all():
        session.delete(env)

    repo = Path(project.repo_path or "")
    if project.repo_path and repo.exists():
        shutil.rmtree(repo, ignore_errors=True)
        actions.append(f"removed workspace:{repo.name}")

    session.commit()
    return actions


def _write_files(root: Path, template: TemplateSpec, context: dict[str, str]) -> None:
    root.mkdir(parents=True, exist_ok=False)
    for rel, content in template.files.items():
        target = (root / rel).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValidationFailed(f"模板文件路径越界: {rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(render(content, context), encoding="utf-8", newline="\n")
    # .gitignore is a convention, not a template variable
    (root / ".gitignore").write_text(
        ".env\nnode_modules/\ndist/\n.venv/\n__pycache__/\n*.pyc\n.next/\ndata/\n", encoding="utf-8"
    )


def _provision_resources(
    session: Session,
    project: Project,
    template: TemplateSpec,
    kinds: list[str],
    context: dict[str, str],
) -> None:
    for kind in kinds:
        if kind == ResourceKind.STORAGE:
            bucket = context["storage_bucket"]
            info = storage_provider().create_bucket(bucket, project_id=project.id)
            session.add(
                ProjectResource(
                    id=new_id("res"),
                    project_id=project.id,
                    kind=ResourceKind.STORAGE,
                    provider=info.provider,
                    name=info.name,
                    endpoint=info.endpoint,
                    status="ready",
                    config=info.meta,
                )
            )
        elif kind == ResourceKind.DATABASE:
            name = context["db_name"]
            info = database_provider().create_database(name, project_id=project.id)
            session.add(
                ProjectResource(
                    id=new_id("res"),
                    project_id=project.id,
                    kind=ResourceKind.DATABASE,
                    provider=info.provider,
                    name=info.name,
                    endpoint=info.endpoint,
                    status="ready",
                    config={"engine": info.engine, "dsn": info.dsn},
                )
            )
    session.commit()


def _set_env(session: Session, project: Project, context: dict[str, str]) -> None:
    values = {
        "DATABASE_URL": context["db_dsn"],
        "STORAGE_BUCKET": context["storage_bucket"],
        "STORAGE_ENDPOINT": (settings.storage_dir / context["storage_bucket"]).as_posix(),
        "PUBLIC_URL": context["fqdn_url"],
        "PORT": context["port"],
    }
    for key, value in values.items():
        session.add(
            EnvVar(
                id=new_id("env"),
                project_id=project.id,
                key=key,
                value=value,
                is_secret=key in {"DATABASE_URL"},
            )
        )
    session.commit()
    env_file = Path(project.repo_path) / ".env"
    env_file.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")


def _bind_domain(session: Session, project: Project, fqdn: str, port: int) -> None:
    target = settings.domain_base_url or f"127.0.0.1:{port}"
    info = domain_provider().bind(fqdn, target=target, project_id=project.id)
    session.add(
        DomainBinding(
            id=new_id("dom"),
            project_id=project.id,
            fqdn=fqdn,
            suffix=settings.domain_suffix,
            provider=info.provider,
            status=info.status,
            target=target,
            is_primary=True,
            auto_generated=True,
        )
    )
    session.commit()


def _git_init(root: Path) -> None:
    """Best effort: a scaffolded project should be a repo from minute zero."""
    try:
        subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.name=AI Workbench", "-c", "user.email=workbench@local",
             "commit", "-qm", "chore: scaffold project"],
            cwd=root,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pass


def get_project(session: Session, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        project = session.exec(select(Project).where(Project.slug == project_id)).first()
    if project is None:
        raise NotFoundError(f"项目不存在: {project_id}")
    return project


def delete_project(session: Session, project: Project, *, purge_files: bool = False) -> None:
    for resource in session.exec(
        select(ProjectResource).where(ProjectResource.project_id == project.id)
    ).all():
        try:
            if resource.kind == ResourceKind.STORAGE:
                storage_provider().delete_bucket(resource.name)
            elif resource.kind == ResourceKind.DATABASE:
                database_provider().drop_database(resource.name)
        except Exception:  # noqa: BLE001 - cleanup must not block deletion
            pass
        session.delete(resource)
    for domain in session.exec(
        select(DomainBinding).where(DomainBinding.project_id == project.id)
    ).all():
        try:
            domain_provider().unbind(domain.fqdn)
        except Exception:  # noqa: BLE001
            pass
        session.delete(domain)
    for env in session.exec(select(EnvVar).where(EnvVar.project_id == project.id)).all():
        session.delete(env)
    session.delete(project)
    session.commit()
    if purge_files:
        shutil.rmtree(project.repo_path, ignore_errors=True)
    log_event(session, "project.deleted", f"项目 {project.name} 已删除")


# ---------------------------------------------------------------------------
# file tree (powers the built-in editor / IDE fallback)
# ---------------------------------------------------------------------------

IGNORED = {"node_modules", ".git", ".venv", "__pycache__", "dist", ".next"}
MAX_FILE_BYTES = 512 * 1024


def project_root(project: Project) -> Path:
    root = Path(project.repo_path).resolve()
    if not root.exists():
        raise NotFoundError("项目文件目录不存在")
    return root


def list_tree(project: Project) -> list[dict]:
    root = project_root(project)
    nodes: list[dict] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED for part in rel.parts):
            continue
        nodes.append(
            {
                "path": rel.as_posix(),
                "name": path.name,
                "type": "dir" if path.is_dir() else "file",
                "size": 0 if path.is_dir() else path.stat().st_size,
            }
        )
    return nodes


def _safe_path(project: Project, rel: str) -> Path:
    root = project_root(project)
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)):
        raise ValidationFailed("非法的文件路径")
    return target


def read_file(project: Project, rel: str) -> dict:
    path = _safe_path(project, rel)
    if not path.is_file():
        raise NotFoundError(f"文件不存在: {rel}")
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValidationFailed("文件过大，请使用 IDE 打开")
    return {"path": rel, "content": path.read_text(encoding="utf-8", errors="replace")}


def write_file(project: Project, rel: str, content: str) -> dict:
    path = _safe_path(project, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")
    return {"path": rel, "size": len(content)}


def delete_file(project: Project, rel: str) -> dict:
    path = _safe_path(project, rel)
    if not path.is_file():
        raise NotFoundError(f"文件不存在: {rel}")
    size = path.stat().st_size
    path.unlink()
    # drop directories that became empty, but never the project root
    root = project_root(project)
    parent = path.parent
    while parent != root and parent.is_dir() and not any(parent.iterdir()):
        parent.rmdir()
        parent = parent.parent
    return {"path": rel, "deleted": True, "bytes": size}
