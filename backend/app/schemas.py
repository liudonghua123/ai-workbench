"""Request bodies. Responses are assembled dicts so the UI can evolve without churn."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    template_key: str = "fullstack-fastapi-nextjs"
    description: str = ""
    slug: str = ""
    type: str = ""
    resources: list[str] = []
    variables: dict[str, str] = {}
    deploy: bool = False
    # defaults to the caller's active organization
    org_id: str = ""
    visibility: str = "organization"  # organization | private


class FileWrite(BaseModel):
    path: str
    content: str


class EnvVarUpsert(BaseModel):
    key: str
    value: str
    is_secret: bool = False


class DeployRequest(BaseModel):
    environment: str = "production"


class PublishRequest(BaseModel):
    message: str = ""


class GitCommitRequest(BaseModel):
    message: str = ""
    paths: list[str] = []


class GitRefRequest(BaseModel):
    path: str = ""
    ref: str = "HEAD"


class DomainCreate(BaseModel):
    subdomain: str
    suffix: str = ""


class McpServerUpsert(BaseModel):
    name: str
    description: str = ""
    transport: str = "streamable_http"
    endpoint: str = ""
    command: str = ""
    args: list[str] = []
    env: dict[str, Any] = {}
    tools: list[dict[str, Any]] = []


class SkillUpsert(BaseModel):
    key: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    source: str = "builtin"
    source_path: str = ""
    enabled: bool = True
    manifest: dict[str, Any] = {}


class AgentUpsert(BaseModel):
    key: str
    name: str
    description: str = ""
    model: str = ""
    system_prompt: str = ""
    mcp_server_ids: list[str] = []
    skill_ids: list[str] = []
    permission_scope: str = "project"


class AgentRunRequest(BaseModel):
    prompt: str
    project_id: str | None = None


# --- identity, organizations, access control -------------------------------


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str = ""
    org_name: str = ""


class PasswordChange(BaseModel):
    current_password: str = ""
    new_password: str


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    slug: str = ""
    description: str = ""
    plan: str = "team"


class OrgUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    plan: str | None = None
    status: str | None = None
    settings: dict[str, Any] | None = None


class MemberAdd(BaseModel):
    email: str = ""
    user_id: str = ""
    name: str = ""
    role_key: str = "viewer"
    title: str = ""


class MemberUpdate(BaseModel):
    role_key: str | None = None
    status: str | None = None
    title: str | None = None


class RoleUpsert(BaseModel):
    key: str = ""
    name: str
    description: str = ""
    permissions: list[str] = []
    rank: int = 20


class BindingCreate(BaseModel):
    user_id: str
    role_key: str = ""
    permissions: list[str] = []
    scope_type: str = "project"
    scope_id: str = ""


class InvitationCreate(BaseModel):
    email: str
    role_key: str = "viewer"
    ttl_days: int = 7


class InvitationAccept(BaseModel):
    token: str


class ProviderUpsert(BaseModel):
    key: str = ""
    kind: str = "oidc"
    name: str = ""
    icon: str = ""
    enabled: bool = False
    config: dict[str, Any] = {}
    secret: dict[str, Any] = {}
    default_role_key: str = "viewer"
    auto_create_users: bool = True
    auto_join: bool = True
    allowed_domains: list[str] = []


class SwitchOrgRequest(BaseModel):
    org_id: str
