from __future__ import annotations

import re
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Body, Depends
from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ConflictError, NotFoundError, ValidationFailed
from app.core.ids import new_id
from app.models import Agent, AgentRun, McpServer, Skill, utcnow
from app.schemas import AgentRunRequest, AgentUpsert, McpServerUpsert, SkillUpsert
from app.services import agent_runtime, skill_files
from app.services.serializers import serialize_agent, serialize_mcp, serialize_run, serialize_skill
from app.services.system_tools import TOOLS

router = APIRouter(tags=["registry"])

SYSTEM_TOOLS = [
    {
        "name": name,
        "description": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else "",
    }
    for name, fn in TOOLS.items()
]


# --- MCP servers -----------------------------------------------------------


@router.get("/mcp-servers")
def list_mcp_servers(session: Session = Depends(get_session)):
    rows = session.exec(select(McpServer).order_by(McpServer.is_system.desc())).all()
    return [serialize_mcp(row) for row in rows]


@router.post("/mcp-servers", status_code=201)
def create_mcp_server(payload: McpServerUpsert, session: Session = Depends(get_session)):
    if session.exec(select(McpServer).where(McpServer.name == payload.name)).first():
        raise ConflictError(f"MCP 服务已存在: {payload.name}")
    row = McpServer(
        id=new_id("mcp"),
        name=payload.name,
        description=payload.description,
        transport=payload.transport,
        endpoint=payload.endpoint,
        command=payload.command,
        args=payload.args,
        env=payload.env,
        tools=payload.tools,
        status="unknown",
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_mcp(row)


@router.patch("/mcp-servers/{server_id}")
def update_mcp_server(
    server_id: str, payload: McpServerUpsert, session: Session = Depends(get_session)
):
    row = session.get(McpServer, server_id)
    if row is None:
        raise NotFoundError("MCP 服务不存在")
    row.description = payload.description or row.description
    row.transport = payload.transport or row.transport
    row.endpoint = payload.endpoint or row.endpoint
    row.command = payload.command or row.command
    row.args = payload.args or row.args
    row.env = payload.env or row.env
    row.tools = payload.tools or row.tools
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_mcp(row)


@router.delete("/mcp-servers/{server_id}", status_code=204)
def delete_mcp_server(server_id: str, session: Session = Depends(get_session)):
    row = session.get(McpServer, server_id)
    if row is None:
        raise NotFoundError("MCP 服务不存在")
    if row.is_system:
        raise ConflictError("内置 MCP 服务不可删除")
    session.delete(row)
    session.commit()


@router.post("/mcp-servers/{server_id}/probe")
async def probe_mcp_server(server_id: str, session: Session = Depends(get_session)):
    """System server returns its local tool list; external servers get a real HTTP probe."""
    row = session.get(McpServer, server_id)
    if row is None:
        raise NotFoundError("MCP 服务不存在")

    if row.is_system:
        row.status = "healthy"
        row.tools = SYSTEM_TOOLS
        row.last_checked_at = utcnow()
        session.add(row)
        session.commit()
        session.refresh(row)
        return serialize_mcp(row)

    error = ""
    tools: list[dict] = row.tools or []
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            headers = {"Accept": "application/json, text/event-stream"}
            response = await client.post(
                row.endpoint,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "ai-workbench", "version": "0.1.0"},
                    },
                },
                headers=headers,
            )
            if response.status_code >= 400:
                error = f"HTTP {response.status_code}"
    except (httpx.HTTPError, OSError) as exc:
        error = str(exc)

    row.status = "error" if error else "healthy"
    row.tools = tools
    row.last_checked_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    payload = serialize_mcp(row)
    payload["probe_error"] = error
    return payload


# --- skills ----------------------------------------------------------------


_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---", re.S)


def _parse_skill_md(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = _FRONTMATTER.match(text)
    if match:
        try:
            return yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError:
            return {}
    return {}


@router.get("/skills")
def list_skills(session: Session = Depends(get_session)):
    rows = session.exec(select(Skill).order_by(Skill.name)).all()
    return [serialize_skill(row) for row in rows]


@router.post("/skills", status_code=201)
def create_skill(payload: SkillUpsert, session: Session = Depends(get_session)):
    if session.exec(select(Skill).where(Skill.key == payload.key)).first():
        raise ConflictError(f"Skill 已存在: {payload.key}")
    row = Skill(
        id=new_id("skl"),
        key=payload.key,
        name=payload.name,
        version=payload.version,
        description=payload.description,
        source=payload.source,
        source_path=payload.source_path,
        enabled=payload.enabled,
        manifest=payload.manifest,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_skill(row)


@router.post("/skills/scan")
def scan_skills(session: Session = Depends(get_session)):
    """Register every ``<dir>/<skill>/SKILL.md`` found under data/skills."""
    root = settings.data_dir / "skills"
    root.mkdir(parents=True, exist_ok=True)
    found = 0
    for skill_md in root.glob("*/SKILL.md"):
        meta = _parse_skill_md(skill_md)
        key = meta.get("name") or skill_md.parent.name
        existing = session.exec(select(Skill).where(Skill.key == key)).first()
        if existing:
            existing.description = meta.get("description", existing.description)
            existing.source_path = skill_md.as_posix()
            existing.manifest = {**existing.manifest, **meta}
            session.add(existing)
        else:
            session.add(
                Skill(
                    id=new_id("skl"),
                    key=key,
                    name=meta.get("title") or key,
                    version=str(meta.get("version", "0.1.0")),
                    description=meta.get("description", ""),
                    source="path",
                    source_path=skill_md.as_posix(),
                    enabled=True,
                    manifest=meta,
                )
            )
        found += 1
    session.commit()
    return {"scanned": found, "root": root.as_posix()}


@router.post("/skills/{skill_id}/toggle")
def toggle_skill(skill_id: str, session: Session = Depends(get_session)):
    row = session.get(Skill, skill_id)
    if row is None:
        raise NotFoundError("Skill 不存在")
    row.enabled = not row.enabled
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_skill(row)


@router.delete("/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str, session: Session = Depends(get_session)):
    row = session.get(Skill, skill_id)
    if row is None:
        raise NotFoundError("Skill 不存在")
    # the directory is the skill's body: a row without it is a stub
    skill_files.remove_dir(row.key)
    session.delete(row)
    session.commit()


# --- skill content (view / edit / install) ---------------------------------


@router.get("/skills/{skill_id}/content")
def skill_content(skill_id: str, session: Session = Depends(get_session)):
    row = session.get(Skill, skill_id)
    if row is None:
        raise NotFoundError("Skill 不存在")
    main = skill_files.read_main(row.key)
    return {
        "skill": serialize_skill(row),
        "dir": row.key,
        "main": main,
        "files": skill_files.list_files(row.key),
    }


@router.put("/skills/{skill_id}/content")
def save_skill_content(
    skill_id: str,
    payload: dict = Body(default_factory=dict),
    session: Session = Depends(get_session),
):
    """Rewrite SKILL.md and re-parse the frontmatter into the row.

    The database keeps metadata, the file keeps the content — saving
    through one without the other is how skills end up lying about
    themselves, so this endpoint writes both.
    """
    row = session.get(Skill, skill_id)
    if row is None:
        raise NotFoundError("Skill 不存在")
    body = str(payload.get("body") or "")
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else None
    text = skill_files.write_main(row.key, body, meta)
    parsed, parsed_body = skill_files.parse_frontmatter(text)
    row.manifest = {**row.manifest, **parsed}
    row.description = str(parsed.get("description") or row.description)
    row.version = str(parsed.get("version") or row.version)
    row.name = str(parsed.get("name") or row.name)
    if row.source == "builtin":
        row.source = "builtin"
    session.add(row)
    session.commit()
    session.refresh(row)
    return {
        "skill": serialize_skill(row),
        "main": {"meta": parsed, "body": parsed_body, "raw": text},
    }


@router.post("/skills/install-url")
def install_skill_from_url(
    payload: dict = Body(default_factory=dict),
    session: Session = Depends(get_session),
):
    """Install from a URL: a SKILL.md, a repo path, or a zip.

    Accepted shapes:
      * ``https://…/SKILL.md`` — direct file
      * ``https://github.com/u/r/blob/branch/path/SKILL.md`` — converted to raw
      * ``https://github.com/u/r/tree/branch/path`` — downloaded as a zip
      * any URL returning ``application/zip`` or ending ``.zip``
    """
    url = str(payload.get("url") or "").strip()
    if not re.match(r"^https?://", url):
        raise ValidationFailed("请提供 http(s) URL")
    key = str(payload.get("key") or "").strip() or None
    with httpx.Client(follow_redirects=True, timeout=30) as client:
        fetch_url = _github_raw_url(url)
        response = client.get(fetch_url)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")

    is_zip = "zip" in content_type or fetch_url.lower().endswith(".zip")
    if is_zip:
        files = skill_files.extract_zip(response.content)
    else:
        if len(response.content) > skill_files.MAX_SKILL_FILE_BYTES:
            raise ValidationFailed("SKILL.md 超过 512KB 上限")
        files = [(Path(url.split("?")[0]).name or "SKILL.md", response.content)]
    row = _install_files(session, files, key=key, source="url", origin=url)
    return serialize_skill(row)


@router.post("/skills/upload")
def upload_skill(
    payload: dict = Body(default_factory=dict),
    session: Session = Depends(get_session),
):
    """Install from pasted content or a base64 zip (no multipart needed).

    ``{name, text}`` installs a single SKILL.md;
    ``{name, archive: <base64>}`` installs an unpacked zip.
    """
    import base64 as _base64

    name = str(payload.get("name") or "").strip() or "skill"
    archive = payload.get("archive")
    if archive:
        try:
            files = skill_files.extract_zip(_base64.b64decode(archive))
        except ValidationFailed:
            raise
        except Exception as exc:  # noqa: BLE001 - bad base64 is a client error
            raise ValidationFailed("无法解码 base64 压缩包") from exc
    else:
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValidationFailed("请提供 SKILL.md 内容或 zip 压缩包")
        if len(text.encode()) > skill_files.MAX_SKILL_FILE_BYTES:
            raise ValidationFailed("SKILL.md 超过 512KB 上限")
        filename = name if name.endswith(".md") else "SKILL.md"
        files = [(filename, text.encode("utf-8"))]
    row = _install_files(session, files, source="upload", origin=name)
    return serialize_skill(row)


@router.get("/skills/hub")
def skill_hub(session: Session = Depends(get_session)):
    """Browse the curated catalog — no account, no API key.

    Entries point at GitHub repos that follow the SKILL.md convention.
    Fetched live and cached briefly; if the network is unavailable the
    built-in list below still renders.
    """
    catalog = [
        {
            "repo": "anthropics/skills",
            "url": "https://github.com/anthropics/skills",
            "description": "Claude 官方技能集：文档处理、代码审查、测试等",
            "tags": ["official", "docs"],
        },
        {
            "repo": "obra/superpowers",
            "url": "https://github.com/obra/superpowers",
            "description": "社区维护的通用技能合集",
            "tags": ["community"],
        },
    ]
    installed = [s.key for s in session.exec(select(Skill)).all()]
    return {"entries": catalog, "installed_keys": installed}


def _github_raw_url(url: str) -> str:
    """Map github.com blob/tree URLs onto raw/codeload endpoints."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/(?:blob|tree)/([^/]+)/(.+)", url)
    if not match:
        return url
    owner, repo, branch, path = match.groups()
    if url.split("?")[0].rstrip("/").endswith((".zip",)):
        return url
    if path.rstrip("/") == path and path.endswith(".md"):
        return f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
    # a directory: grab the whole subtree as a zip
    return (
        f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{branch}"
        if "/" not in path.rstrip("/")
        else url
    )


def _install_files(
    session: Session,
    files: list[tuple[str, bytes]],
    *,
    source: str,
    origin: str,
    key: str | None = None,
) -> Skill:
    """Register + write a skill from raw files; update in place if it exists."""
    main_payload = next((payload for name, payload in files if Path(name).name == "SKILL.md"), None)
    if main_payload is None:
        raise ValidationFailed("缺少 SKILL.md — 技能包必须包含一个 SKILL.md")

    meta, _ = skill_files.parse_frontmatter(main_payload.decode("utf-8", errors="replace"))
    skill_key = key or skill_files.key_from_name(meta.get("name") or origin)
    existing = session.exec(select(Skill).where(Skill.key == skill_key)).first()
    skill_files.write_files(skill_key, files)
    if existing:
        existing.description = str(meta.get("description") or existing.description)
        existing.version = str(meta.get("version") or existing.version)
        existing.name = str(meta.get("name") or existing.name)
        existing.source = source
        existing.source_path = f"skills/{skill_key}/SKILL.md"
        existing.manifest = {**existing.manifest, **meta}
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing
    row = Skill(
        id=new_id("skl"),
        key=skill_key,
        name=str(meta.get("name") or skill_key),
        version=str(meta.get("version") or "0.1.0"),
        description=str(meta.get("description") or ""),
        source=source,
        source_path=f"skills/{skill_key}/SKILL.md",
        enabled=True,
        manifest={**meta, **{"files": [name for name, _ in files]}},
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


# --- agents ----------------------------------------------------------------


@router.get("/agents")
def list_agents(session: Session = Depends(get_session)):
    rows = session.exec(select(Agent)).all()
    return [serialize_agent(row, session) for row in rows]


@router.post("/agents", status_code=201)
def create_agent(payload: AgentUpsert, session: Session = Depends(get_session)):
    if session.exec(select(Agent).where(Agent.key == payload.key)).first():
        raise ConflictError(f"Agent 已存在: {payload.key}")
    row = Agent(
        id=new_id("agt"),
        key=payload.key,
        name=payload.name,
        description=payload.description,
        model=payload.model or settings.default_agent_model,
        system_prompt=payload.system_prompt,
        mcp_server_ids=payload.mcp_server_ids,
        skill_ids=payload.skill_ids,
        permission_scope=payload.permission_scope,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_agent(row, session)


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, payload: AgentUpsert, session: Session = Depends(get_session)):
    row = session.get(Agent, agent_id) or session.exec(
        select(Agent).where(Agent.key == agent_id)
    ).first()
    if row is None:
        raise NotFoundError("Agent 不存在")
    row.name = payload.name or row.name
    row.description = payload.description or row.description
    row.model = payload.model or row.model
    row.system_prompt = payload.system_prompt or row.system_prompt
    row.mcp_server_ids = payload.mcp_server_ids or row.mcp_server_ids
    row.skill_ids = payload.skill_ids or row.skill_ids
    row.permission_scope = payload.permission_scope or row.permission_scope
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return serialize_agent(row, session)


@router.delete("/agents/{agent_id}", status_code=204)
def delete_agent(agent_id: str, session: Session = Depends(get_session)):
    row = session.get(Agent, agent_id)
    if row is None:
        raise NotFoundError("Agent 不存在")
    session.delete(row)
    session.commit()


@router.post("/agents/{agent_key}/run")
def run_agent(agent_key: str, payload: AgentRunRequest):
    return agent_runtime.run(agent_key, payload.prompt, project_id=payload.project_id)


@router.get("/agent-runs")
def list_runs(agent_id: str | None = None, session: Session = Depends(get_session)):
    stmt = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(50)  # type: ignore[attr-defined]
    if agent_id:
        stmt = (
            select(AgentRun)
            .where(AgentRun.agent_id == agent_id)
            .order_by(AgentRun.created_at.desc())  # type: ignore[attr-defined]
            .limit(50)
        )
    rows = session.exec(stmt).all()
    return [serialize_run(row) for row in rows]
