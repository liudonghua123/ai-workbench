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
            label = result.get("name") or result.get("slug")
            lines.append(f"- 项目入口：{result['url']}" + (f"（{label}）" if label else ""))
        if result.get("tag"):
            signature = f"tag:{result['tag']}"
            if signature not in seen:
                seen.add(signature)
                commit = result.get("commit") if isinstance(result.get("commit"), dict) else {}
                lines.append(
                    f"- 发布 {result.get('version', '')} · {result['tag']}"
                    f"（commit {commit.get('short') or 'n/a'}）"
                )
        for key in ("deployment", "latest"):
            dep = result.get(key)
            if isinstance(dep, dict) and dep.get("version"):
                signature = f"{dep.get('version')}:{dep.get('status')}"
                if signature in seen:
                    continue
                seen.add(signature)
                status = dep.get("status")
                dep_line = f"- 部署 {dep.get('version')}：{status} {dep.get('url') or ''}"
                lines.append(dep_line.rstrip())
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


SYSTEM_PROMPT = """你是 AI Workbench 的操盘 Agent。你可以通过工具真实地创建、修改、调试、发布应用。

工作台约定（约定大于配置，不要自己发明路径、端口或域名）：
- 三类项目：fullstack（backend/ + frontend/）、frontend（只有静态产物）、backend（app/main.py）。
  先调用 check_project_conventions 拿到该类型的目录布局、命令与体检结果，再动手。
- 项目工作区：data/projects/<slug>；对象存储桶 <slug>-assets；数据库名 slug 中的 "-" 换成 "_"。
- 生产域名：<slug>.<域名后缀>；调试（预览）域名：<slug>-dev.<域名后缀>，独立端口、独立进程。
- 平台自动注入 DATABASE_URL / STORAGE_BUCKET / STORAGE_ENDPOINT / PUBLIC_URL / PORT /
  PREVIEW_URL / DEV_PORT，应用只读不写；不要硬编码域名。
- 唯一允许的配置文件是项目根目录的 workbench.yaml（可选，任意字段），其他一切按约定推导。

工作流：
1. 先看再动：check_project_conventions + list_project_files / search_project / read_project_file。
2. 一次只做一个可验证的动作；写文件写完整内容，不要写 diff 或省略号。
3. 调试：start_preview，然后 preview_status 读 log_tail；失败按日志修，不要盲目重试。
4. 发布：publish_project（自带 git commit + tag deploy/v<N> + 部署该 commit），再用
   deployment_status 确认；需要在发布前留下可回滚点就先用 git_commit。
5. 回滚：git_log 找到目标 sha → git_revert_commit → 再 publish_project。
6. 查数据库只能读（SELECT / WITH / EXPLAIN）。
7. 工具报错不要重复同样的调用，换参数或换思路；三次失败就停下来向用户说明。
8. 最终回答用中文，简洁说明做了什么、预览/生产地址、下一步建议；不要罗列内部 JSON。

权限边界（重要）：
- 你继承调用者的权限：同一个工具，开发者能调、只读成员不能调。403 不是 bug，是边界。
- 同样继承数据范围：调用者是 own 范围时，list_projects 只返回他拥有或被授权的项目，
  别人的项目一律 404。看不到就说明"不在你的范围内"，不要换个 id 反复试。
- 动手前若不确定，先 describe_access 看自己的角色、权限集与 data_scope，
  或 check_permission 预检单个权限。
- 不要试图绕过：不要改配置、不要伪造身份、不要在只读库里写。被拒绝就如实告诉用户谁该做什么。
- 组织与身份源：list_organizations / list_org_members / list_sso_providers 可读；
  SSO 连接（CAS / OAuth2 / OIDC / 企业微信 / 钉钉）的密钥只存在服务端，任何工具都拿不到。
"""


class LlmHarness(Harness):
    """Function-calling loop. Model-agnostic; tools come from ``system_tools``."""

    name = "llm"

    def execute(self, task: AgentTask) -> tuple[str, list[Step]]:
        steps: list[Step] = []
        schemas = llm.tool_schemas(task.tools)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": "工作台现状：" + json.dumps(describe_workbench(), ensure_ascii=False),
            },
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
            conclusion = (
                f"达到最大步数上限（{settings.agent_max_steps}），已停止。"
                "你可以继续下达指令让我接着做。"
            )

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

        def target() -> str | None:
            """Resolve the project the prompt is about, without asking the user.

            Matching on slug *and* name before falling back to the first project
            matters: "debug customer-success" must not silently operate on whatever
            happens to be created first.
            """
            if task.project_id:
                return task.project_id
            projects = call("需要确定目标项目，先列出工作台项目", "list_projects") or []
            for item in projects:
                for key in (item.get("slug"), item.get("name")):
                    if key and str(key).lower() in lowered:
                        return item["id"]
            return projects[0]["id"] if projects else None

        wants_create = bool(re.search(r"创建|新建|生成|搭建|create|scaffold|new app", lowered))
        wants_publish = bool(re.search(r"发布|上线|release|publish|ship", lowered))
        wants_preview = bool(re.search(r"预览|调试|debug|preview|dev server", lowered))
        wants_run = bool(re.search(r"运行|启动|跑起来|\brun\b|start", lowered))
        wants_revert = bool(re.search(r"回滚|revert|rollback|退回到", lowered))
        wants_discard = bool(re.search(r"撤销|放弃|discard|还原", lowered))
        wants_git = bool(re.search(r"版本|提交|commit|\bgit\b|历史|history", lowered))
        wants_convention = bool(re.search(r"规范|约定|体检|convention|conformance", lowered))
        wants_access = bool(
            re.search(r"权限|角色|成员|组织|sso|单点|rbac|permission|access|member|role", lowered)
        )
        wants_deploy = bool(re.search(r"部署|deploy", lowered))
        wants_status = bool(re.search(r"状态|日志|status|log", lowered))
        wants_list = bool(re.search(r"列出|列表|所有项目|list", lowered))

        if wants_create:
            template_key = next(
                (
                    key
                    for key, hints in self.TEMPLATE_HINTS.items()
                    if any(h in lowered for h in hints)
                ),
                "fullstack-fastapi-nextjs",
            )
            name = self._extract_name(prompt)
            created = call(
                f"选定模板 {template_key} 并创建工作区、对象存储、数据库，同时分配生产与预览域名",
                "create_project",
                name=name,
                template_key=template_key,
                description=prompt[:160],
            )
            if not isinstance(created, dict):
                return render_report(prompt, steps), steps
            call(
                "读取该类型的规范与体检结果", "check_project_conventions", project_id=created["id"]
            )
            if wants_preview or wants_run or wants_publish:
                call("启动预览环境", "start_preview", project_id=created["id"])
                call("读取预览状态与日志", "preview_status", project_id=created["id"])
            if wants_publish:
                call(
                    "提交并发布首个版本",
                    "publish_project",
                    project_id=created["id"],
                    message="首次发布",
                )
                call("确认部署状态", "deployment_status", project_id=created["id"])
            return render_report(prompt, steps), steps

        project_id = target()

        # Same order the system prompt asks for: read the type spec before touching
        # anything. Without this, "检查规范然后启动预览" would only do the second half.
        if wants_convention and project_id:
            call(
                "按项目类型体检，确认目录、命令与契约",
                "check_project_conventions",
                project_id=project_id,
            )

        if wants_revert:
            if project_id:
                history = (
                    call(
                        "读取提交历史以定位回滚点", "git_log", project_id=project_id, limit=10
                    )
                    or []
                )
                if history:
                    call(
                        "回退最近一次提交（生成新提交）",
                        "git_revert_commit",
                        project_id=project_id,
                        sha=history[0]["sha"],
                    )
                    if wants_publish:
                        call(
                            "发布回滚后的版本",
                            "publish_project",
                            project_id=project_id,
                            message="回滚发布",
                        )
            return render_report(prompt, steps), steps

        if wants_discard:
            if project_id:
                state = call("读取工作区改动", "git_status", project_id=project_id) or {}
                for item in (state.get("files") or [])[:20]:
                    call(
                        f"还原 {item['path']}",
                        "git_restore_file",
                        project_id=project_id,
                        path=item["path"],
                    )
            return render_report(prompt, steps), steps

        if wants_publish:
            if project_id:
                call(
                    "提交并发布当前工作区",
                    "publish_project",
                    project_id=project_id,
                    message=prompt[:80],
                )
                call("确认部署状态", "deployment_status", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_preview:
            if project_id:
                call("启动或重启预览环境", "start_preview", project_id=project_id)
                call("读取预览状态与日志尾部", "preview_status", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_deploy:
            if project_id:
                call("触发生产部署", "deploy_project", project_id=project_id)
                call("读取部署日志尾部", "deployment_status", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_git:
            if project_id:
                call("读取工作区状态", "git_status", project_id=project_id)
                call("读取提交历史", "git_log", project_id=project_id, limit=10)
            return render_report(prompt, steps), steps

        if wants_convention:
            # the check itself already ran above when a project was resolved
            if not project_id:
                call("读取工作台约定与工具面", "describe_workbench")
            return render_report(prompt, steps), steps

        if wants_access:
            # answers "what may I do" from the caller's real permission set
            call("读取调用者的角色与权限集", "describe_access")
            call("列出可访问的组织", "list_organizations")
            call("列出组织成员", "list_org_members")
            call("读取已配置的身份源", "list_sso_providers")
            return render_report(prompt, steps), steps

        if wants_run:
            if project_id:
                call("按约定运行项目", "run_project", project_id=project_id)
                call("读取运行状态", "deployment_status", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_status:
            if project_id:
                call("读取最新部署与日志", "deployment_status", project_id=project_id)
                call("读取预览状态", "preview_status", project_id=project_id)
                call("读取项目资源清单", "get_project", project_id=project_id)
            return render_report(prompt, steps), steps

        if wants_list:
            call("列出全部项目及资源", "list_projects")
            return render_report(prompt, steps), steps

        # default: a read-only sweep so the answer is grounded
        if project_id:
            call("读取目标项目", "get_project", project_id=project_id)
            call("按类型体检", "check_project_conventions", project_id=project_id)
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

        if project_id:
            # Fail before a run row exists: a foreign id should look like a typo,
            # not like "the run happened and every tool mysteriously failed".
            from app.core import context
            from app.core.security import require_project

            principal = context.current_principal()
            if principal is not None:
                project_id = require_project(session, principal, project_id).id

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
                    "preview_status",
                    "list_project_files",
                    "read_project_file",
                    "search_project",
                    "git_status",
                    "git_log",
                    "git_diff",
                    "check_project_conventions",
                    "list_env_vars",
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
            first = Step(
                index=1, thought=fallback_note, tool="__harness__", ok=False, error=exc.message
            )
            steps = _reindex([first] + exc.steps + rest)
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
