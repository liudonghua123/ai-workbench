"""Agent runtime with a swappable harness.

A *harness* owns the reasoning loop; the workbench owns the tools. The default
``LocalHarness`` is a deterministic intent router — no model key required, so the
feature is testable and demoable offline. Plug a real framework (agentscope,
langgraph, your own ReAct loop) by subclassing :class:`Harness` and registering it;
nothing else changes because tools are plain callables from ``system_tools``.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.core.errors import NotFoundError
from app.core.ids import new_id
from app.models import Agent, AgentRun, Event, utcnow
from app.services.system_tools import TOOLS


@dataclass(slots=True)
class Step:
    index: int
    thought: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    ok: bool = True
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        preview = self.result
        if isinstance(preview, list):
            preview = preview[:3]
        text = repr(preview)
        return {
            "index": self.index,
            "thought": self.thought,
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "error": self.error,
            "result_preview": text[:800],
        }


@dataclass(slots=True)
class AgentTask:
    agent: dict[str, Any]
    prompt: str
    project_id: str | None
    tools: dict[str, Any]


class Harness(ABC):
    name: str = "abstract"

    @abstractmethod
    def execute(self, task: AgentTask) -> tuple[str, list[Step]]: ...


class LocalHarness(Harness):
    """Keyword intent router. Deterministic, dependency-free, honest about it."""

    name = "local"

    TEMPLATE_HINTS = {
        "frontend-vite-react": ("前端", "frontend", "页面", "官网", "landing", "静态"),
        "backend-fastapi": ("后端", "backend", "api", "接口", "服务"),
        "agent-fastapi": ("agent", "智能体", "mcp", "harness"),
        "fullstack-fastapi-nextjs": ("全栈", "fullstack", "前后端", "管理台", "dashboard"),
    }

    def execute(self, task: AgentTask) -> tuple[str, list[Step]]:
        steps: list[Step] = []
        prompt = task.prompt.strip()
        lowered = prompt.lower()

        def call(thought: str, tool: str, **kwargs: Any) -> Any:
            step = Step(index=len(steps) + 1, thought=thought, tool=tool, args=kwargs)
            try:
                fn = task.tools.get(tool)
                if fn is None:
                    raise KeyError(f"tool not available: {tool}")
                step.result = fn(**kwargs)
            except Exception as exc:  # noqa: BLE001 - the run must not die on one tool
                step.ok = False
                step.error = f"{type(exc).__name__}: {exc}"
            steps.append(step)
            return step.result

        wants_create = bool(re.search(r"创建|新建|生成|搭建|create|scaffold|new app", lowered))
        wants_deploy = bool(re.search(r"部署|上线|发布|deploy|release", lowered))
        wants_status = bool(re.search(r"状态|日志|status|log", lowered))
        wants_list = bool(re.search(r"列出|列表|所有项目|list", lowered))

        if wants_create:
            template_key = next(
                (key for key, hints in self.TEMPLATE_HINTS.items() if any(h in lowered for h in hints)),
                "fullstack-fastapi-nextjs",
            )
            name = self._extract_name(prompt)
            created = call(
                f"选定模板 {template_key} 并创建工作区、对象存储、数据库与 <slug>.{settings.domain_suffix} 域名",
                "create_project",
                name=name,
                template_key=template_key,
                description=prompt[:160],
            )
            if isinstance(created, dict) and wants_deploy:
                call("按约定执行首次部署并等待健康检查", "deploy_project", project_id=created["id"])
            if isinstance(created, dict):
                call("回读项目状态，确认资源与域名绑定", "get_project", project_id=created["id"])
            return self._report(prompt, steps), steps

        project_id = task.project_id

        if wants_deploy:
            if project_id is None:
                projects = call("需要确定目标项目，先列出工作台项目", "list_projects") or []
                project_id = projects[0]["id"] if projects else None
            if project_id:
                call("触发部署", "deploy_project", project_id=project_id)
                call("读取部署日志尾部", "deployment_status", project_id=project_id)
            return self._report(prompt, steps), steps

        if wants_status:
            if project_id is None:
                projects = call("列出项目以定位目标", "list_projects") or []
                project_id = projects[0]["id"] if projects else None
            if project_id:
                call("读取最新部署与日志", "deployment_status", project_id=project_id)
                call("读取项目资源清单", "get_project", project_id=project_id)
            return self._report(prompt, steps), steps

        if wants_list:
            call("列出全部项目及资源", "list_projects")
            return self._report(prompt, steps), steps

        # default: a read-only sweep so the answer is grounded
        if project_id:
            call("读取目标项目", "get_project", project_id=project_id)
            call("读取部署状态", "deployment_status", project_id=project_id)
            call("读取文件清单", "list_project_files", project_id=project_id, limit=40)
        else:
            call("读取工作台项目概览", "list_projects")
            call("读取可用模板", "list_templates")
        return self._report(prompt, steps), steps

    def _extract_name(self, prompt: str) -> str:
        quoted = re.findall(r"[「“\"']([^」”\"']{2,40})[」”\"']", prompt)
        if quoted:
            return quoted[0]
        cleaned = re.sub(
            r"(帮我|请|创建一个|创建|新建|生成|搭建|一个|项目|应用|app|create|a|an|project)",
            " ",
            prompt,
            flags=re.IGNORECASE,
        )
        tokens = [t for t in cleaned.split() if len(t) >= 2]
        return (tokens[0] if tokens else "新应用")[:32]

    def _report(self, prompt: str, steps: list[Step]) -> str:
        lines = [f"**请求**：{prompt}", "", "**执行轨迹**"]
        for step in steps:
            mark = "OK" if step.ok else "ERR"
            lines.append(f"{step.index}. [{mark}] `{step.tool}` — {step.thought}")
            if step.error:
                lines.append(f"    └ {step.error}")
        ok = [s for s in steps if s.ok]
        lines += ["", f"**结果**：{len(ok)}/{len(steps)} 个工具调用成功。"]
        for step in ok:
            if isinstance(step.result, dict) and step.result.get("url"):
                lines.append(f"- 项目入口：{step.result['url']}")
            if isinstance(step.result, dict) and step.result.get("deployment"):
                dep = step.result["deployment"]
                lines.append(f"- 部署 {dep.get('version')}：{dep.get('status')} {dep.get('url') or ''}")
            if isinstance(step.result, dict) and step.result.get("latest"):
                latest = step.result["latest"]
                lines.append(f"- 部署 {latest.get('version')}：{latest.get('status')} {latest.get('url') or ''}")
        return "\n".join(lines)


_HARNESSES: dict[str, Harness] = {}


def register_harness(harness: Harness) -> Harness:
    _HARNESSES[harness.name] = harness
    return harness


def get_harness(name: str = "local") -> Harness:
    if name not in _HARNESSES:
        register_harness(LocalHarness())
    return _HARNESSES.get(name, _HARNESSES["local"])


def run(agent_key: str, prompt: str, project_id: str | None = None) -> dict[str, Any]:
    with Session(engine) as session:
        agent = session.exec(select(Agent).where(Agent.key == agent_key)).first()
        if agent is None:
            agent = session.exec(select(Agent).where(Agent.id == agent_key)).first()
        if agent is None:
            raise NotFoundError(f"Agent 不存在: {agent_key}")

        run_row = AgentRun(
            id=new_id("run"),
            agent_id=agent.id,
            project_id=project_id,
            prompt=prompt,
            status="running",
        )
        session.add(run_row)
        session.commit()

        agent_dict = {
            "id": agent.id,
            "key": agent.key,
            "name": agent.name,
            "model": agent.model or settings.default_agent_model,
            "permission_scope": agent.permission_scope,
        }

        # An agent only gets the tools its permission scope allows.
        allowed = dict(TOOLS)
        if agent.permission_scope == "readonly":
            allowed = {
                k: v
                for k, v in TOOLS.items()
                if k
                in {
                    "list_templates",
                    "list_projects",
                    "get_project",
                    "deployment_status",
                    "list_project_files",
                    "read_project_file",
                    "list_agents",
                }
            }

        harness = get_harness()
        output, steps = harness.execute(
            AgentTask(agent=agent_dict, prompt=prompt, project_id=project_id, tools=allowed)
        )

        run_row.status = "succeeded" if all(s.ok for s in steps) else "failed"
        run_row.output = output
        run_row.steps = [s.to_dict() for s in steps]
        run_row.finished_at = utcnow()
        session.add(run_row)
        session.add(
            Event(
                id=new_id("evt"),
                kind="agent.run",
                project_id=project_id,
                message=f"Agent {agent.name} 执行：{prompt[:80]}",
                payload={"run_id": run_row.id, "harness": harness.name},
            )
        )
        session.commit()

        return {
            "run_id": run_row.id,
            "agent": agent_dict,
            "harness": harness.name,
            "status": run_row.status,
            "output": output,
            "steps": run_row.steps,
        }
