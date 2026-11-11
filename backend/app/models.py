"""Persistence models.

Conventions:
* ids are prefixed opaque strings (``proj_ab12cd``) — safe to expose in URLs.
* every mutable table carries ``created_at`` / ``updated_at`` (UTC).
* free-form extensibility lives in ``meta`` / ``config`` JSON columns instead of
  scattering one-off columns. That way new template or provider options do not
  require a migration.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import Column, Text
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ProjectType(StrEnum):
    FULLSTACK = "fullstack"
    FRONTEND = "frontend"
    BACKEND = "backend"
    AGENT = "agent"


class ProjectStatus(StrEnum):
    CREATING = "creating"
    READY = "ready"
    FAILED = "failed"
    ARCHIVED = "archived"


class ResourceKind(StrEnum):
    STORAGE = "storage"
    DATABASE = "database"


class DeploymentStatus(StrEnum):
    QUEUED = "queued"
    BUILDING = "building"
    DEPLOYING = "deploying"
    RUNNING = "running"
    FAILED = "failed"
    STOPPED = "stopped"


class DomainStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"


class McpTransport(StrEnum):
    STDIO = "stdio"
    SSE = "sse"
    HTTP = "streamable_http"


class _Base(SQLModel):
    pass


class Project(_Base, table=True):
    __tablename__ = "projects"

    id: str = Field(primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    type: str = Field(default=ProjectType.FULLSTACK, index=True)
    template_key: str = ""
    template_version: str = ""
    status: str = Field(default=ProjectStatus.CREATING, index=True)
    repo_path: str = ""
    runtime: str = ""  # python | node | static
    run_command: str = ""
    port: int | None = None
    primary_url: str = ""
    git_initialized: bool = False
    # principal name that created the project; empty when auth is disabled
    owner_id: str = Field(default="", index=True)
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class ProjectResource(_Base, table=True):
    __tablename__ = "project_resources"

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, foreign_key="projects.id")
    kind: str = Field(default=ResourceKind.STORAGE, index=True)
    provider: str = "local"
    name: str
    endpoint: str = ""
    status: str = "ready"
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow)


class DomainBinding(_Base, table=True):
    __tablename__ = "domain_bindings"

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, foreign_key="projects.id")
    fqdn: str = Field(index=True, unique=True)
    suffix: str = ""
    provider: str = "local"
    status: str = Field(default=DomainStatus.PENDING)
    target: str = ""
    is_primary: bool = False
    auto_generated: bool = True
    created_at: datetime = Field(default_factory=utcnow)


class EnvVar(_Base, table=True):
    __tablename__ = "env_vars"

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, foreign_key="projects.id")
    key: str
    value: str = ""
    scope: str = "all"  # all | production | preview
    is_secret: bool = False


class Deployment(_Base, table=True):
    __tablename__ = "deployments"

    id: str = Field(primary_key=True)
    project_id: str = Field(index=True, foreign_key="projects.id")
    environment: str = "production"
    status: str = Field(default=DeploymentStatus.QUEUED, index=True)
    version: str = ""
    image: str = ""
    url: str = ""
    provider: str = "local"
    log: str = Field(default="", sa_column=Column(Text, default=""))
    error: str = ""
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None


class McpServer(_Base, table=True):
    __tablename__ = "mcp_servers"

    id: str = Field(primary_key=True)
    name: str = Field(index=True, unique=True)
    description: str = ""
    transport: str = Field(default=McpTransport.HTTP)
    endpoint: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    env: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    status: str = "unknown"  # unknown | healthy | error
    is_system: bool = False
    tools: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    last_checked_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Skill(_Base, table=True):
    __tablename__ = "skills"

    id: str = Field(primary_key=True)
    key: str = Field(index=True, unique=True)
    name: str
    version: str = "0.1.0"
    description: str = ""
    source: str = "builtin"  # builtin | path | url
    source_path: str = ""
    enabled: bool = True
    manifest: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow)


class Agent(_Base, table=True):
    __tablename__ = "agents"

    id: str = Field(primary_key=True)
    key: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    model: str = ""
    system_prompt: str = Field(default="", sa_column=Column(Text, default=""))
    mcp_server_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    skill_ids: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    permission_scope: str = "project"  # readonly | project | full
    status: str = "ready"
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AgentRun(_Base, table=True):
    __tablename__ = "agent_runs"

    id: str = Field(primary_key=True)
    agent_id: str = Field(index=True, foreign_key="agents.id")
    project_id: str | None = Field(default=None, index=True)
    prompt: str = Field(default="", sa_column=Column(Text, default=""))
    status: str = "pending"  # pending | running | succeeded | failed
    output: str = Field(default="", sa_column=Column(Text, default=""))
    steps: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    finished_at: datetime | None = None


class Event(_Base, table=True):
    __tablename__ = "events"

    id: str = Field(primary_key=True)
    kind: str = Field(index=True)
    project_id: str | None = Field(default=None, index=True)
    message: str = ""
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow, index=True)
