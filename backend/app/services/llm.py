"""Minimal OpenAI-compatible chat client + tool-schema generation.

Why not the vendor SDK? The workbench must talk to *any* endpoint that speaks
``POST {base_url}/chat/completions`` — OpenAI, Azure, vLLM, Ollama, DashScope's
compatible mode, an internal gateway. Twenty lines of httpx buy that portability
without pinning a provider.

Tool schemas are generated from the very same plain functions the REST layer and
MCP server expose, by reading their signature and docstring. Adding an agent
capability therefore never means writing a JSON schema by hand.
"""

from __future__ import annotations

import types
from typing import Any, Callable, Union, get_args, get_origin, get_type_hints

import httpx

from app.core.config import settings


class LlmError(RuntimeError):
    """Raised when the model endpoint is unreachable or returns an error."""


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def configured() -> bool:
    return bool(settings.llm_api_key.strip())


def model_name() -> str:
    return settings.llm_model.strip() or settings.default_agent_model


def status() -> dict[str, Any]:
    """Everything the settings page needs, with the key redacted."""
    return {
        "configured": configured(),
        "base_url": settings.llm_base_url,
        "model": model_name(),
        "harness": effective_harness(),
        "requested_harness": settings.agent_harness,
        "timeout_s": settings.llm_timeout,
        "max_steps": settings.agent_max_steps,
        "note": (
            "已配置模型端点，Agent 由 LLM 规划"
            if configured()
            else "未配置 WB_LLM_API_KEY，Agent 使用内置规则规划器（功能可用，理解力有限）"
        ),
    }


def effective_harness() -> str:
    """Resolve ``WB_AGENT_HARNESS=auto`` against the presence of a key."""
    requested = (settings.agent_harness or "auto").lower()
    if requested == "llm":
        return "llm"
    if requested == "local":
        return "local"
    return "llm" if configured() else "local"


# ---------------------------------------------------------------------------
# transport
# ---------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }


def chat(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """One non-streaming completion. Returns the assistant ``message`` object."""
    if not configured():
        raise LlmError("未配置 WB_LLM_API_KEY")

    payload: dict[str, Any] = {
        "model": model_name(),
        "messages": messages,
        "temperature": settings.llm_temperature if temperature is None else temperature,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    try:
        with httpx.Client(timeout=timeout or settings.llm_timeout) as client:
            response = client.post(url, headers=_headers(), json=payload)
    except httpx.HTTPError as exc:
        raise LlmError(f"无法连接模型端点: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:400]
        raise LlmError(f"模型端点返回 {response.status_code}: {detail}")

    try:
        data = response.json()
        return data["choices"][0]["message"]
    except (ValueError, KeyError, IndexError) as exc:
        raise LlmError(f"模型返回结构异常: {response.text[:300]}") from exc


def probe() -> dict[str, Any]:
    """Cheap round-trip used by the settings page's “test connection” button."""
    if not configured():
        return {"ok": False, "error": "未配置 WB_LLM_API_KEY", "configured": False}
    try:
        message = chat(
            [
                {"role": "system", "content": "Reply with the single word: pong"},
                {"role": "user", "content": "ping"},
            ],
            temperature=0,
            timeout=min(settings.llm_timeout, 30),
        )
    except LlmError as exc:
        return {"ok": False, "error": str(exc), "configured": True, "model": model_name()}
    return {
        "ok": True,
        "configured": True,
        "model": model_name(),
        "reply": (message.get("content") or "").strip()[:200],
        "latency_note": "measured server-side",
    }


# ---------------------------------------------------------------------------
# JSON schema from python signatures
# ---------------------------------------------------------------------------

_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}


def _json_type(annotation: Any) -> dict[str, Any]:
    if annotation is Any or annotation is None:
        return {"type": "string"}
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        # Optional[X] -> schema of X (the field simply isn't required)
        return _json_type(args[0]) if args else {"type": "string"}
    if origin in (list, tuple, set):
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}
    if origin is dict:
        return {"type": "object"}
    if annotation in _PRIMITIVES:
        return {"type": _PRIMITIVES[annotation]}
    return {"type": "string"}


def _optional(annotation: Any) -> bool:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        return type(None) in get_args(annotation)
    return False


def tool_schema(fn: Callable[..., Any], *, name: str | None = None) -> dict[str, Any]:
    """Build an OpenAI function-tool definition from a python callable."""
    import inspect

    signature = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:  # noqa: BLE001 - unresolved forward refs are not fatal
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in signature.parameters.items():
        annotation = hints.get(param_name, param.annotation)
        schema = _json_type(annotation)
        prop: dict[str, Any] = {"title": param_name, **schema}
        has_default = param.default is not inspect.Parameter.empty
        if has_default and param.default is not None:
            prop["default"] = param.default
        if not has_default and not _optional(annotation):
            required.append(param_name)
        properties[param_name] = prop

    description = (inspect.getdoc(fn) or "").strip() or f"Call {name or fn.__name__}"
    return {
        "type": "function",
        "function": {
            "name": name or fn.__name__,
            "description": description[:1024],
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def tool_schemas(tools: dict[str, Callable[..., Any]]) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for tool_name, fn in tools.items():
        try:
            schemas.append(tool_schema(fn, name=tool_name))
        except Exception:  # noqa: BLE001 - a bad signature must not kill the run
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": (fn.__doc__ or tool_name).strip()[:1024],
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            )
    return schemas
