"""A fake OpenAI-compatible endpoint for offline testing of the LLM harness.

It is not a model: it replays a canned function-calling turn so the *plumbing* —
tool-schema generation, tool execution, second turn, report rendering — can be
verified without a key, a network, or a bill.

    python tools/mock_llm.py            # listens on 127.0.0.1:8811
    WB_LLM_BASE_URL=http://127.0.0.1:8811/v1 WB_LLM_API_KEY=test

Turn 1: calls ``list_templates`` (no arguments beyond the schema).
Turn 2: answers in prose, echoing how many tools ran.
"""

from __future__ import annotations

import json

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="mock-llm")


class ChatRequest(BaseModel):
    model: str = "mock"
    messages: list[dict] = []
    tools: list[dict] | None = None
    tool_choice: str | None = None
    temperature: float | None = None


def _reply(message: dict) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": "mock",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
def chat(request: ChatRequest):
    tool_messages = [m for m in request.messages if m.get("role") == "tool"]
    available = {tool["function"]["name"] for tool in request.tools or []}

    if not tool_messages and "list_templates" in available:
        return _reply(
            {
                "role": "assistant",
                "content": "先看看有哪些模板可用。",
                "tool_calls": [
                    {
                        "id": "call_mock_1",
                        "type": "function",
                        "function": {"name": "list_templates", "arguments": "{}"},
                    }
                ],
            }
        )

    summary = "; ".join(
        f"{m.get('name')} -> {str(m.get('content'))[:80]}" for m in tool_messages
    )
    return _reply(
        {
            "role": "assistant",
            "content": f"已通过工具取到信息（共 {len(tool_messages)} 次调用）。{summary}",
        }
    )


@app.get("/v1/models")
def models():
    return {"data": [{"id": "mock", "object": "model"}]}


@app.get("/health")
def health():
    return {"status": "ok", "tool_calls": json.dumps(["list_templates"])}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8811, log_level="warning")
