"""Agent runtime with a swappable harness.

A *harness* owns the reasoning loop; the workbench owns the tools. Two ship today:

* :class:`LlmHarness` — a real function-calling loop against any OpenAI-compatible
  endpoint. Used automatically when ``WB_LLM_API_KEY`` is set.
* :class:`LocalHarness` — a deterministic intent router. No key required, so the
  feature stays demoable offline and is the automatic fallback when the model
  endpoint is down.

Both call the same ``{name: callable}`` map from ``system_tools``, so "the agent can
create and modify applications" needs no special-casing anywhere. Plug agentscope /
langgraph by subclassing :class:`Harness` and calling :func:`register_harness`.
"""

from __future__ import annotations

import json
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
from app.services import llm
from app.services.system_tools import TOOLS, describe_workbench


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
        return {
            "index": self.index,
            "thought": self.thought,
            "tool": self.tool,
            "args": self.args,
            "ok": self.ok,
            "error": self.error,
            "result_preview": repr(preview)[:800],
        }


@dataclass(slots=True)
class AgentTask:
    agent: dict[str, Any]
    prompt: str
    project_id: str | None
    tools: dict[str, Any]


class HarnessError(RuntimeError):
    """Raised when a harness cannot finish. Carries the steps it did complete."""

    def __init__(self, message: str, steps: list[Step] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.steps = steps or []


class Harness(ABC):
    name: str = "abstract"

    @abstractmethod
    def execute(self, task: AgentTask) -> tuple[str, list[Step]]: ...


# ---------------------------------------------------------------------------
# shared rendering
# ---------------------------------------------------------------------------


def _highlights(steps: list[Step]) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if not step.ok or not isinstance(step.result, dict):
            continue
        result = step.result
        if result.get("url") and result.get("url") not in seen:
            seen.add(result["url"])
            lines.append(f"- 项目入口：{result['url']}（{result.get('name') or result.get('slug')}）")
        for key in ("deployment", "latest"):
            dep = result.get(key)
            if isinstance(dep, dict) and dep.get("version"):
                signature = f"{dep.get('version')}:{dep.get('status')}"
                if signature in seen:
                    continue
                seen.add(signature)
                lines.append(
                    f"- 部署 {dep.get('version')}：{dep.get('status')} {dep.get('url') or ''}".rstrip()
                )
    return lines


def render_report(prompt: str, steps: list[Step], conclusion: str | None = None) -> str:
    lines = [f"**请求**：{prompt}", ""]
    if conclusion:
        lines += [conclusion.strip(), ""]
    lines.append("**执行轨迹**")
    for step in steps:
        mark = "OK" if step.ok else "ERR"
        lines.append(f"{step.index}. [{mark}] `{step.tool}` — {step.thought}")
        if step.error:
            lines.append(f"    └ {step.error}")
    ok = [s for s in steps if s.ok]
    lines += ["", f"**结果**：{len(ok)}/{len(steps)} 个工具调用成功。"]
    lines += _highlights(steps)
    return "\n".join(line for line in lines if line is not None)


def _reindex(steps: list[Step]) -> list[Step]:
    for position, step in enumerate(steps, start=1):
        step.index = position
    return steps


# ---------------------------------------------------------------------------
# LLM harness
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = """你是 AI Workbench 的操盘 Agent。你可以通过工具真实地创建、修改、部署应用。

工作台约定（约定大于配置，不要自己发明路径或名字）：
- 项目工作区：data/projects/<slug>
- 对象存储桶：<slug>-assets
- 数据库名：slug 中的 "-" 换成 "_"
- 主域名：<slug>.<域名后缀>
- 每个项目自动注入 DATABASE_URL / STORAGE_BUCKET / STORAGE_ENDPOINT / PUBLIC_URL / PORT

规则：
1. 先看再动。修改前用 get_project / list_project_files / read_project_file 确认现状。
2. 一次只做一个可验证的动作，拿到工具结果再决定下一步。
3. 写文件要写完整内容，不要写 diff、不要写省略号。
4. 改完代码要 deploy_project，再用 deployment_status 确认状态，失败要看 log_tail。
5. 查数据库只能读（SELECT / WITH / EXPLAIN），不要尝试写库或改 schema。
6. 工具报错不要重复同样的调用，换参数或换思路；三次失败就停下来向用户说明。
7. 最终回答用中文，简洁说明做了什么、结果如何、下一步建议；不要罗列内部 JSON。
"""


class LlmHarness(Harness):
    """Function-calling loop. Model-agnostic; tools come from ``system_tools``."""

    name = "llm"

    def execute(self, task: AgentTask) -> tuple[str, list[Step]]:
        steps: list[Step] = []
        schemas = llm.tool_schemas(task.tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "工作台现状：" + json.dumps(describe_workbench(), ensure_ascii=False)},
            {"role": "user", "content": self._user_message(task)},
        ]
        conclusion = ""

        for _ in range(max(2, settings.agent_max_steps)):
            try:
                message = llm.chat(messages, tools=schemas)
            except llm.LlmError as exc:
                raise HarnessError(f"模型调用失败: {exc}", _reindex(steps)) from exc

            narration = (message.get("content") or "").strip()
            calls = message.get("tool_calls") or []
            if not calls:
                conclusion = narration
                break

            messages.append(
                {
                    "role": "assistant",
                    "content": narration or None,
                    "tool_calls": calls,
                }
            )

            for call in calls:
                function = call.get("function") or {}
                tool_name = function.get("name") or ""
                raw_args = function.get("arguments")
                args, parse_error = self._parse_args(raw_args)
                step = Step(
                    index=len(steps) + 1,
                    thought=narration or f"调用 `{tool_name}`",
                    tool=tool_name,
                    args=args,
                )
                if parse_error:
                    step.ok = False
                    step.error = parse_error
                else:
                    step.result, step.ok, step.error = self._invoke(task.tools, tool_name, args)
                steps.append(step)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id") or f"call_{len(steps)}",
                        "name": tool_name,
                        "content": self._tool_payload(step),
                    }
                )
        else:
            conclusion = f"达到最大步数上限（{settings.agent_max_steps}），已停止。你可以继续下达指令让我接着做。"

        return render_report(task.prompt, steps, conclusion), steps

    # -- helpers ----------------------------------------------------------
    def _user_message(self, task: AgentTask) -> str:
        context = ""
        if task.project_id:
            context = f"\n（当前上下文项目 id：{task.project_id}）"
        return f"{task.prompt}{context}"

    @staticmethod
    def _parse_args(raw: Any) -> tuple[dict[str, Any], str]:
        if raw is None or raw == "":
            return {}, ""
        if isinstance(raw, dict):
            return raw, ""
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError) as exc:
            return {}, f"工具参数不是合法 JSON: {exc}"
        if not isinstance(parsed, dict):
            return {}, "工具参数必须是 JSON 对象"
        return parsed, ""

    @staticmethod
    def _invoke(
        tools: dict[str, Any], tool_name: str, args: dict[str, Any]
    ) -> tuple[Any, bool, str]:
        fn = tools.get(tool_name)
        if fn is None:
            available = ", ".join(sorted(tools))
            return None, False, f"工具不存在: {tool_name}（可用: {available}）"
        try:
            return fn(**args), True, ""
        except TypeError as exc:
            return None, False, f"参数不匹配: {exc}"
        except Exception as exc:  # noqa: BLE001 - one failed tool must not kill the run
            return None, False, f"{type(exc).__name__}: {exc}"

    @staticmethod
    def _tool_payload(step: Step) -> str:
        if not step.ok:
            return json.dumps({"ok": False, "error": step.error}, ensure_ascii=False)
        try:
            body = json.dumps(step.result, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            body = repr(step.result)
        if len(body) > 6000:
            body = body[:6000] + "…(truncated)"
        return body


# ---------------------------------------------------------------------------
# local harness
# ---------------------------------------------------------------------------


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
            return render_report(prompt, steps), steps

        project_id = task.project_id

        if wants_deploy:
            if project_id is None:
                projects = call("需要确定目标项目，先列出工作台项目", "list_projects") or []
                project_id = projects[0]["id"] if projects else None
            if project_id:
                call("触发部署", "deploy_project", project_id=project_id)
                call("读取部署日志尾部", "deployment_status", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_status:
            if project_id is None:
                projects = call("列出项目以定位目标", "list_projects") or []
                project_id = projects[0]["id"] if projects else None
            if project_id:
                call("读取最新部署与日志", "deployment_status", project_id=project_id)
                call("读取项目资源清单", "get_project", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_list:
            call("列出全部项目及资源", "list_projects")
            return render_report(prompt, steps), steps

        # default: a read-only sweep so the answer is grounded
        if project_id:
            call("读取目标项目", "get_project", project_id=project_id)
            call("读取部署状态", "deployment_status", project_id=project_id)
            call("读取文件清单", "list_project_files", project_id=project_id, limit=40)
        else:
            call("读取工作台项目概览", "list_projects")
            call("读取可用模板", "list_templates")
        return render_report(prompt, steps), steps

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


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_HARNESSES: dict[str, Harness] = {}


def register_harness(harness: Harness) -> Harness:
    _HARNESSES[harness.name] = harness
    return harness


def get_harness(name: str = "local") -> Harness:
    if "local" not in _HARNESSES:
        register_harness(LocalHarness())
    if "llm" not in _HARNESSES:
        register_harness(LlmHarness())
    return _HARNESSES.get(name) or _HARNESSES["local"]


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
            "model": agent.model or llm.model_name(),
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
                    "describe_workbench",
                    "list_templates",
                    "list_projects",
                    "get_project",
                    "deployment_status",
                    "list_project_files",
                    "read_project_file",
                    "list_agents",
                    "query_database",
                }
            }

        task = AgentTask(agent=agent_dict, prompt=prompt, project_id=project_id, tools=allowed)
        harness_name = llm.effective_harness()
        harness = get_harness(harness_name)
        used = harness.name
        fallback_note = ""

        try:
            output, steps = harness.execute(task)
        except HarnessError as exc:
            fallback_note = f"{harness.name} 未能完成（{exc.message}），已回退到内置规划器"
            _, rest = get_harness("local").execute(task)
            steps = _reindex(
                [Step(index=1, thought=fallback_note, tool="__harness__", ok=False, error=exc.message)]
                + exc.steps
                + rest
            )
            output = render_report(prompt, steps)
            used = "local (fallback)"

        # a harness-level failure that was recovered by the fallback is not a failed run
        judged = [s for s in steps if s.tool != "__harness__"]
        run_row.status = "succeeded" if all(s.ok for s in judged) else "failed"
        run_row.output = output
        run_row.steps = [s.to_dict() for s in steps]
        run_row.finished_at = utcnow()
        session.add(run_row)
        session.add(
            Event(
                id=new_id("evt"),
                kind="agent.fallback" if fallback_note else "agent.run",
                project_id=project_id,
                message=(
                    f"Agent {agent.name} 降级为本地规划器：{fallback_note}"
                    if fallback_note
                    else f"Agent {agent.name} 执行：{prompt[:80]}"
                ),
                payload={"run_id": run_row.id, "harness": used},
            )
        )
        session.commit()

        return {
            "run_id": run_row.id,
            "agent": agent_dict,
            "harness": used,
            "status": run_row.status,
            "output": output,
            "steps": run_row.steps,
            "fallback": bool(fallback_note),
        }
