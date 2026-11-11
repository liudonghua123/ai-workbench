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
