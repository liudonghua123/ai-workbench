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
from app.services.system_tools import TOOLS

mcp = FastMCP(
    name="ai-workbench",
    instructions=(
        "AI Workbench control plane. Use list_templates before create_project, "
        "create_project provisions storage + database + a <slug>.<suffix> domain, "
        "deploy_project then deployment_status to verify, and write_project_file / "
        "set_env_var to modify a running application."
    ),
)


def _register() -> None:
    for name, fn in TOOLS.items():
        mcp.tool(fn, name=name)


def _extra_tools() -> None:
    @mcp.tool(name="run_workbench_agent")
    def run_workbench_agent(agent_key: str, prompt: str, project_id: str | None = None) -> dict[str, Any]:
        """Delegate a high-level goal to a configured workbench agent (it plans and calls tools)."""
        return run_agent_harness(agent_key, prompt, project_id=project_id)

    @mcp.tool(name="workbench_capabilities")
    def workbench_capabilities() -> dict[str, Any]:
        """Describe the tool surface and object model so an agent can plan safely."""
        return {
            "object_model": ["project", "resource(storage|database)", "domain", "deployment", "agent"],
            "tools": sorted([*TOOLS, "run_workbench_agent", "workbench_capabilities"]),
            "conventions": {
                "domain": "<project-slug>.<WB_DOMAIN_SUFFIX>",
                "bucket": "<project-slug>-assets",
                "workspace": "data/projects/<project-slug>",
            },
        }


_register()
_extra_tools()


def http_app():
    """Starlette app exposing the streamable HTTP endpoint at ``/mcp``."""
    return mcp.http_app(path="/mcp")
