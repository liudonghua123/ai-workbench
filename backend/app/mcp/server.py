"""The workbench's own MCP server.

Mounted on the same FastAPI app at ``/mcp`` (streamable HTTP), so one process, one
port, two protocols. Tools are the *same* callables the REST layer and the agent
harness use — no parallel implementation to drift.

Client config example:

    {
      "mcpServers": {
        "workbench": { "url": "http://127.0.0.1:8000/mcp" }
      }
    }
"""

from __future__ import annotations

from typing import Any

from fastmcp import FastMCP

from app.services.agent_runtime import run as run_agent_harness
from app.services.system_tools import TOOLS, describe_workbench

mcp = FastMCP(
    name="ai-workbench",
    instructions=(
        "AI Workbench control plane. Read check_project_conventions first — it returns the "
        "type spec (layout, dev/build/run commands, health path) plus a pass/warn/fail report. "
        "create_project provisions storage + database + <slug>.<suffix> and a debug domain "
        "<slug>-dev.<suffix>. Edit with write_project_file / search_project, verify with "
        "start_preview + preview_status (log_tail), then publish_project, which commits, tags "
        "deploy/v<N> and deploys that exact commit. Use git_status / git_diff / git_log before "
        "touching files you did not write, and git_revert_commit to roll back.\n\n"
        "Authorisation: the caller's organization membership decides what these tools may do. "
        "Call describe_access to see your role and permission set, and check_permission before "
        "an expensive or destructive step — a 403 means the human you are acting for cannot do "
        "it either. list_sso_providers / list_org_members are read-only views of the access "
        "plane; identity providers themselves are configured in the UI (secrets are never "
        "returned by the API)."
    ),
)


def _register() -> None:
    for name, fn in TOOLS.items():
        mcp.tool(fn, name=name)


def _extra_tools() -> None:
    @mcp.tool(name="run_workbench_agent")
    def run_workbench_agent(
        agent_key: str, prompt: str, project_id: str | None = None
    ) -> dict[str, Any]:
        """Delegate a high-level goal to a configured workbench agent (it plans and calls tools)."""
        return run_agent_harness(agent_key, prompt, project_id=project_id)

    @mcp.tool(name="workbench_capabilities")
    def workbench_capabilities() -> dict[str, Any]:
        """Describe the tool surface, conventions and runtime so an agent can plan safely."""
        report = describe_workbench()
        report["object_model"] = [
            "project",
            "resource(storage|database)",
            "domain(production|preview)",
            "deployment(preview|production)",
            "commit/tag",
            "agent",
            "organization > membership(role) > permission",
            "identity_provider(cas|oauth2|oidc|wecom|dingtalk)",
            "audit_log",
        ]
        report["tools"] = sorted([*TOOLS, "run_workbench_agent", "workbench_capabilities"])
        return report


_register()
_extra_tools()


def http_app():
    """Starlette app exposing the streamable HTTP endpoint at ``/mcp``."""
    return mcp.http_app(path="/mcp")
