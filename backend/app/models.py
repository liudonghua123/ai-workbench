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


class UserStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class MemberStatus(StrEnum):
    ACTIVE = "active"
    INVITED = "invited"
    SUSPENDED = "suspended"


class OrgStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"


class IdentityKind(StrEnum):
    """Supported identity sources.

    ``local`` is the password store, ``mock`` is the OIDC provider the workbench
    hosts itself (it exercises the exact same code path as a real IdP, which is
    how the SSO plumbing stays testable offline).
    """

    LOCAL = "local"
    CAS = "cas"
    OAUTH2 = "oauth2"
    OIDC = "oidc"
    WECOM = "wecom"
    DINGTALK = "dingtalk"
    MOCK = "mock"


class ScopeType(StrEnum):
    ORGANIZATION = "organization"
    PROJECT = "project"


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
    # production: build_command (optional) -> run_command on <slug>.<suffix>
    build_command: str = ""
    run_command: str = ""
    port: int | None = None
    # preview/debug: dev_command on <slug>-dev.<suffix>, always a second port so a
    # running preview never fights the production process for a socket.
    dev_command: str = ""
    dev_port: int | None = None
    preview_fqdn: str = ""
    preview_url: str = ""
    health_path: str = "/"
    primary_url: str = ""
    git_initialized: bool = False
    # principal name that created the project; empty when auth is disabled
    owner_id: str = Field(default="", index=True)
    # organization that owns the project; "" only for rows created before orgs existed
    org_id: str = Field(default="", index=True)
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
    # git commit this deployment was built from — the link between "what runs" and
    # "what is in the repository", so a rollback is a commit sha and not a guess.
    commit_sha: str = ""
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


# ---------------------------------------------------------------------------
# Identity, organizations and access control
#
# Shape of the model — deliberately small enough to reason about:
#
#     User ──< Membership >── Organization ──< Role
#      │            (one org-level role)          (permission sets)
#      ├──< Identity                     (external subject from an IdP)
#      ├──< RoleBinding                  (extra grant, scoped to one project)
#      └──< Session                      (opaque bearer token, hashed at rest)
#
# There is no permission column on the user: authority is always
# "membership role ∪ project bindings", resolved per request. That is what makes
# revocation immediate instead of "next time they log in".
# ---------------------------------------------------------------------------


class User(_Base, table=True):
    __tablename__ = "users"

    id: str = Field(primary_key=True)
    email: str = Field(default="", index=True)
    name: str = ""
    avatar: str = ""
    status: str = Field(default=UserStatus.ACTIVE, index=True)
    # scrypt hash produced by app.core.passwords — empty for SSO-only accounts
    password_hash: str = Field(default="", sa_column=Column(Text, default=""))
    # platform operators bypass org boundaries; never grant this through the UI alone
    is_platform_admin: bool = False
    locale: str = ""
    last_login_at: datetime | None = None
    meta: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Organization(_Base, table=True):
    __tablename__ = "organizations"

    id: str = Field(primary_key=True)
    slug: str = Field(index=True, unique=True)
    name: str
    description: str = ""
    plan: str = "team"
    status: str = Field(default=OrgStatus.ACTIVE, index=True)
    owner_id: str = Field(default="", index=True)
    settings: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Role(_Base, table=True):
    """A named permission set inside one organization.

    Builtin roles are seeded per org and carry ``builtin=True`` so the UI refuses
    to delete them. Custom roles are just rows with a different ``permissions``
    list — no code change, no migration.
    """

    __tablename__ = "roles"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    key: str = Field(index=True)
    name: str
    description: str = ""
    permissions: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    builtin: bool = False
    rank: int = 10
    created_at: datetime = Field(default_factory=utcnow)


class Membership(_Base, table=True):
    __tablename__ = "memberships"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role_key: str = "viewer"
    status: str = Field(default=MemberStatus.ACTIVE, index=True)
    title: str = ""
    invited_by: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class RoleBinding(_Base, table=True):
    """An extra grant that survives outside the org-level role.

    Used for "this contractor may deploy, but only on *that* project". Bindings
    are additive and always expire-or-revoke by deletion, never by rewriting the
    membership.
    """

    __tablename__ = "role_bindings"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    user_id: str = Field(index=True)
    role_key: str = ""
    permissions: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    scope_type: str = Field(default=ScopeType.PROJECT, index=True)
    scope_id: str = Field(default="", index=True)
    created_by: str = ""
    created_at: datetime = Field(default_factory=utcnow)


class Identity(_Base, table=True):
    """A verified external subject, linked to exactly one user."""

    __tablename__ = "identities"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    provider_key: str = Field(index=True)
    kind: str = Field(default=IdentityKind.LOCAL, index=True)
    subject: str = Field(index=True)
    email: str = ""
    display_name: str = ""
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    last_login_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class IdentityProvider(_Base, table=True):
    """Connection settings for one CAS / OAuth2 / OIDC / WeCom / DingTalk tenant.

    ``config`` holds everything safe to show a browser. ``secret`` holds client
    secrets and is *never* serialised — API responses expose ``has_secret``
    instead, which is enough to render "already configured" states.
    """

    __tablename__ = "identity_providers"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)  # "" = platform-wide
    key: str = Field(index=True, unique=True)
    kind: str = Field(default=IdentityKind.OIDC, index=True)
    name: str
    icon: str = ""
    enabled: bool = False
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    secret: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    default_role_key: str = "viewer"
    auto_create_users: bool = True
    auto_join: bool = True
    allowed_domains: list[str] = Field(default_factory=list, sa_column=Column(JSON, default=list))
    status: str = "unconfigured"  # unconfigured | ready | error
    last_check_at: datetime | None = None
    last_error: str = ""
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Session(_Base, table=True):
    """Bearer session. Only the SHA-256 of the token is stored."""

    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    token_hash: str = Field(index=True, unique=True)
    org_id: str = ""
    kind: str = "session"  # session | bootstrap
    ip: str = ""
    user_agent: str = ""
    revoked_at: datetime | None = None
    expires_at: datetime
    last_seen_at: datetime = Field(default_factory=utcnow)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class Invitation(_Base, table=True):
    __tablename__ = "invitations"

    id: str = Field(primary_key=True)
    org_id: str = Field(index=True)
    email: str = Field(index=True)
    role_key: str = "viewer"
    token_hash: str = Field(default="", index=True)
    invited_by: str = ""
    status: str = "pending"  # pending | accepted | revoked
    expires_at: datetime | None = None
    accepted_at: datetime | None = None
    created_at: datetime = Field(default_factory=utcnow, index=True)


class AuthRequest(_Base, table=True):
    """Short-lived state for an in-flight SSO round trip.

    One row per ``GET /auth/{key}/start``. It carries the PKCE verifier and the
    post-login destination, so the callback can stay stateless and still be
    replay-safe (the row is deleted on first use).
    """

    __tablename__ = "auth_requests"

    id: str = Field(primary_key=True)
    state: str = Field(index=True, unique=True)
    provider_key: str = Field(index=True)
    org_id: str = ""
    redirect_uri: str = ""
    return_to: str = "/"
    code_verifier: str = ""
    nonce: str = ""
    created_at: datetime = Field(default_factory=utcnow, index=True)
    expires_at: datetime


class AuditLog(_Base, table=True):
    """Append-only record of every access-control decision worth explaining later."""

    __tablename__ = "audit_logs"

    id: str = Field(primary_key=True)
    org_id: str = Field(default="", index=True)
    actor_id: str = Field(default="", index=True)
    actor_name: str = ""
    action: str = Field(index=True)
    resource_type: str = ""
    resource_id: str = ""
    status: str = "ok"  # ok | denied | error
    ip: str = ""
    detail: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, default=dict))
    created_at: datetime = Field(default_factory=utcnow, index=True)
