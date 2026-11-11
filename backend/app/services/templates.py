"""Template registry — the "convention over configuration" half of the product.

A template is a *spec*, not a directory listing you maintain by hand. Two sources
ship by default:

* :class:`BuiltinTemplateSource` — versioned in code, always available, no disk IO.
* :class:`DirectoryTemplateSource` — drop a folder with ``template.yaml`` under
  ``WB_TEMPLATE_DIRS`` and it appears in the UI, no restart of the API needed.

Both satisfy :class:`TemplateSource`, so a marketplace/remote source can be added
later without touching the scaffolder.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


@dataclass(slots=True)
class TemplateVariable:
    key: str
    label: str
    default: str = ""
    required: bool = False
    help: str = ""


@dataclass(slots=True)
class TemplateSpec:
    key: str
    name: str
    type: str
    description: str
    version: str = "1.0.0"
    icon: str = "box"
    tags: list[str] = field(default_factory=list)
    runtime: str = ""
    run_command: str = ""
    resources: list[str] = field(default_factory=lambda: ["storage", "database"])
    variables: list[TemplateVariable] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    notes: str = ""
    source: str = "builtin"

    def to_public(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "type": self.type,
            "description": self.description,
            "version": self.version,
            "icon": self.icon,
            "tags": self.tags,
            "runtime": self.runtime,
            "run_command": self.run_command,
            "resources": self.resources,
            "variables": [
                {
                    "key": v.key,
                    "label": v.label,
                    "default": v.default,
                    "required": v.required,
                    "help": v.help,
                }
                for v in self.variables
            ],
            "file_count": len(self.files),
            "files": sorted(self.files),
            "notes": self.notes,
            "source": self.source,
        }


class TemplateSource(ABC):
    @abstractmethod
    def list(self) -> list[TemplateSpec]: ...

    @abstractmethod
    def get(self, key: str) -> TemplateSpec | None: ...


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------


def render(text: str, context: dict[str, str]) -> str:
    def _sub(match: re.Match[str]) -> str:
        key = match.group(1)
        return str(context.get(key, match.group(0)))

    return PLACEHOLDER_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# builtin templates
# ---------------------------------------------------------------------------

_README_TMPL = """# {{project_name}}

{{description}}

> Scaffolded by AI Workbench from template `{{template_key}}` v{{template_version}}.

## At a glance

| | |
|---|---|
| Type | `{{project_type}}` |
| Preview | {{fqdn_url}} |
| Object storage | `{{storage_bucket}}` |
| Database | `{{db_dsn}}` |
| API base | `{{api_base}}` |

## Develop

```bash
{{dev_command}}
```

## Deploy

Deployments are driven by the Workbench control plane; every push is packaged,
the domain binding is re-verified and the health check must pass before traffic
is switched over.
"""

_FRONTEND_APP = """import { useEffect, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE ?? "{{api_base}}";

export default function App() {
  const [health, setHealth] = useState<string>("checking...");

  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => (r.ok ? r.json() : Promise.reject(r.statusText)))
      .then((d) => setHealth(d.status ?? "ok"))
      .catch(() => setHealth("backend not connected"));
  }, []);

  return (
    <main className="shell">
      <h1>{{project_name}}</h1>
      <p className="sub">{{description}}</p>
      <div className="card">
        <span className="dot" /> backend: <code>{health}</code>
      </div>
      <footer>
        storage <code>{{storage_bucket}}</code> · db <code>{{db_name}}</code>
      </footer>
    </main>
  );
}
"""

_FRONTEND_CSS = """@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap");

:root {
  color-scheme: light dark;
  --bg: #f6f7fb;
  --fg: #101322;
  --card: #ffffff;
  --line: #e4e7f0;
  --accent: #5b5bd6;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b0d14;
    --fg: #eef0f8;
    --card: #141824;
    --line: #232838;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif;
  background: radial-gradient(120% 120% at 10% 0%, color-mix(in srgb, var(--accent) 18%, var(--bg)), var(--bg));
  color: var(--fg);
  min-height: 100vh;
  display: grid;
  place-items: center;
}

.shell { width: min(680px, 92vw); }

h1 { font-size: clamp(28px, 5vw, 44px); margin: 0 0 8px; letter-spacing: -0.02em; }

.sub { opacity: 0.72; margin: 0 0 28px; }

.card {
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 16px;
  padding: 18px 20px;
  display: flex;
  align-items: center;
  gap: 10px;
  box-shadow: 0 18px 40px -28px rgba(16, 19, 34, 0.5);
}

.dot { width: 8px; height: 8px; border-radius: 50%; background: #22c55e; }

footer { margin-top: 22px; font-size: 13px; opacity: 0.6; }
code { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
"""

_FASTAPI_MAIN = """from __future__ import annotations

import os

from fastapi import FastAPI

app = FastAPI(title="{{project_name}}", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "{{slug}}"}


@app.get("/api/info")
def info() -> dict[str, object]:
    return {
        "storage_bucket": os.getenv("STORAGE_BUCKET", "{{storage_bucket}}"),
        "database_url": os.getenv("DATABASE_URL", "{{db_dsn}}"),
        "public_url": os.getenv("PUBLIC_URL", "{{fqdn_url}}"),
    }
"""

_AGENT_MAIN = """from __future__ import annotations

from fastapi import FastAPI
from fastmcp import FastMCP

app = FastAPI(title="{{project_name}}", version="0.1.0")
mcp = FastMCP("{{slug}}")

# The harness (agentscope / your own loop) plugs in here. Keeping the tool surface
# on MCP means the same functions are usable by a human, by the workbench agent and
# by any external client.
STATE: dict[str, str] = {"note": "hello from {{slug}}"}


@mcp.tool
def read_note() -> str:
    \"\"\"Read the current note.\"\"\"
    return STATE["note"]


@mcp.tool
def write_note(text: str) -> str:
    \"\"\"Replace the note.\"\"\"
    STATE["note"] = text
    return "ok"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "mcp": "/mcp"}


app.mount("/mcp", mcp.http_app(path="/"))
"""

_NEXT_PAGE = """const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "{{api_base}}";

async function getHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`, { cache: "no-store" });
    return res.ok ? await res.json() : { status: "unreachable" };
  } catch {
    return { status: "unreachable" };
  }
}

export default async function Home() {
  const health = await getHealth();
  return (
    <main style={{ fontFamily: "Inter, system-ui, sans-serif", padding: "64px 28px" }}>
      <p style={{ letterSpacing: "0.18em", fontSize: 11, opacity: 0.55 }}>AI WORKBENCH PROJECT</p>
      <h1 style={{ fontSize: 40, margin: "8px 0 6px", letterSpacing: "-0.02em" }}>{{project_name}}</h1>
      <p style={{ opacity: 0.7, margin: 0 }}>{{description}}</p>
      <pre style={{ marginTop: 32, padding: 18, borderRadius: 14, background: "#0d1117", color: "#c9d1d9", overflowX: "auto" }}>
{JSON.stringify(health, null, 2)}
      </pre>
    </main>
  );
}
"""


def _builtin() -> list[TemplateSpec]:
    common_vars = [
        TemplateVariable("description", "项目描述", "由 AI Workbench 创建的应用", help="一句话说明"),
    ]
    return [
        TemplateSpec(
            key="fullstack-fastapi-nextjs",
            name="全栈 · FastAPI + Next.js",
            type="fullstack",
            description="前后端分离的标准全栈骨架：FastAPI 提供 API 与 MCP，Next.js 负责界面。",
            tags=["fastapi", "nextjs", "recommended"],
            icon="layers",
            runtime="node+python",
            run_command="npm --prefix frontend run dev",
            variables=common_vars,
            files={
                "backend/pyproject.toml": '[project]\nname = "{{slug}}-api"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "fastmcp>=2.3"]\n\n[tool.uv]\npackage = false\n',
                "backend/app/__init__.py": "",
                "backend/app/main.py": _FASTAPI_MAIN.replace(
                    'app = FastAPI(title="{{project_name}}", version="0.1.0")',
                    'from fastmcp import FastMCP\n\napp = FastAPI(title="{{project_name}}", version="0.1.0")\nmcp = FastMCP("{{slug}}")\n\n\n@mcp.tool\ndef project_info() -> dict[str, object]:\n    """Return project metadata for agents."""\n    return info()',
                )
                + '\n\napp.mount("/mcp", mcp.http_app(path="/"))\n',
                "frontend/package.json": '{\n  "name": "{{slug}}-web",\n  "private": true,\n  "scripts": { "dev": "vite --port {{port}} --host", "build": "vite build", "preview": "vite preview" },\n  "dependencies": { "react": "^19.0.0", "react-dom": "^19.0.0" },\n  "devDependencies": { "@vitejs/plugin-react": "^4.3.4", "typescript": "^5.7.2", "vite": "^6.0.5" }\n}\n',
                "frontend/vite.config.ts": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n  server: { port: {{port}}, strictPort: true },\n});\n',
                "frontend/index.html": '<!doctype html>\n<html lang="zh-CN">\n  <head>\n    <meta charset="utf-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1" />\n    <title>{{project_name}}</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.tsx"></script>\n  </body>\n</html>\n',
                "frontend/src/main.tsx": 'import React from "react";\nimport { createRoot } from "react-dom/client";\nimport App from "./App";\nimport "./styles.css";\n\ncreateRoot(document.getElementById("root")!).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n);\n',
                "frontend/src/App.tsx": _FRONTEND_APP,
                "frontend/src/styles.css": _FRONTEND_CSS,
                ".env.example": "DATABASE_URL={{db_dsn}}\nSTORAGE_BUCKET={{storage_bucket}}\nPUBLIC_URL={{fqdn_url}}\nAPI_BASE={{api_base}}\n",
                "README.md": _README_TMPL,
            },
            notes="前后端各自独立部署；前端通过 VITE_API_BASE 指向后端。",
        ),
        TemplateSpec(
            key="frontend-vite-react",
            name="纯前端 · Vite + React",
            type="frontend",
            description="零后端依赖的静态站点，构建产物直接托管，适合门户、看板、活动页。",
            tags=["vite", "react", "static"],
            icon="layout",
            runtime="node",
            run_command="npm run dev",
            resources=["storage"],
            variables=common_vars,
            files={
                "package.json": '{\n  "name": "{{slug}}",\n  "private": true,\n  "scripts": { "dev": "vite --port {{port}} --host", "build": "vite build", "preview": "vite preview --port {{port}}" },\n  "dependencies": { "react": "^19.0.0", "react-dom": "^19.0.0" },\n  "devDependencies": { "@vitejs/plugin-react": "^4.3.4", "typescript": "^5.7.2", "vite": "^6.0.5" }\n}\n',
                "vite.config.ts": 'import { defineConfig } from "vite";\nimport react from "@vitejs/plugin-react";\n\nexport default defineConfig({\n  plugins: [react()],\n  server: { port: {{port}}, strictPort: true },\n});\n',
                "index.html": '<!doctype html>\n<html lang="zh-CN">\n  <head>\n    <meta charset="utf-8" />\n    <meta name="viewport" content="width=device-width, initial-scale=1" />\n    <title>{{project_name}}</title>\n  </head>\n  <body>\n    <div id="root"></div>\n    <script type="module" src="/src/main.tsx"></script>\n  </body>\n</html>\n',
                "src/main.tsx": 'import React from "react";\nimport { createRoot } from "react-dom/client";\nimport App from "./App";\nimport "./styles.css";\n\ncreateRoot(document.getElementById("root")!).render(\n  <React.StrictMode>\n    <App />\n  </React.StrictMode>,\n);\n',
                "src/App.tsx": _FRONTEND_APP,
                "src/styles.css": _FRONTEND_CSS,
                "README.md": _README_TMPL,
            },
            notes="纯静态：部署产物是 dist/，可直传对象存储。",
        ),
        TemplateSpec(
            key="backend-fastapi",
            name="纯后端 · FastAPI + MCP",
            type="backend",
            description="API 服务骨架，自带 /health、/api/info 与同端口 /mcp 的 streamable HTTP MCP。",
            tags=["fastapi", "api", "mcp"],
            icon="server",
            runtime="python",
            run_command="python -m uvicorn app.main:app --host 0.0.0.0 --port {{port}}",
            variables=common_vars,
            files={
                "pyproject.toml": '[project]\nname = "{{slug}}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "fastmcp>=2.3"]\n\n[tool.uv]\npackage = false\n',
                "app/__init__.py": "",
                "app/main.py": _FASTAPI_MAIN.replace(
                    'app = FastAPI(title="{{project_name}}", version="0.1.0")',
                    'from fastmcp import FastMCP\n\napp = FastAPI(title="{{project_name}}", version="0.1.0")\nmcp = FastMCP("{{slug}}")\n\n\n@mcp.tool\ndef project_info() -> dict[str, object]:\n    """Return project metadata for agents."""\n    return info()',
                )
                + '\n\napp.mount("/mcp", mcp.http_app(path="/"))\n',
                "Dockerfile": 'FROM python:3.12-slim\nWORKDIR /app\nCOPY . .\nRUN pip install --no-cache-dir fastapi uvicorn fastmcp\nEXPOSE {{port}}\nCMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "{{port}}"]\n',
                ".env.example": "DATABASE_URL={{db_dsn}}\nSTORAGE_BUCKET={{storage_bucket}}\nPUBLIC_URL={{fqdn_url}}\n",
                "README.md": _README_TMPL,
            },
            notes="无界面。调用方通过 PUBLIC_URL 访问，MCP 客户端连 /mcp。",
        ),
        TemplateSpec(
            key="agent-fastapi",
            name="Agent 服务 · FastAPI + MCP + Harness",
            type="agent",
            description="以 MCP 暴露工具、以 harness（agentscope 等）驱动推理的 Agent 服务骨架。",
            tags=["agent", "mcp", "agentscope"],
            icon="cpu",
            runtime="python",
            run_command="python -m uvicorn app.main:app --host 0.0.0.0 --port {{port}}",
            variables=[
                TemplateVariable("description", "项目描述", "一个可被工作台编排的 Agent 服务"),
                TemplateVariable("model", "模型", "workbuddy-flash"),
            ],
            files={
                "pyproject.toml": '[project]\nname = "{{slug}}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "fastmcp>=2.3", "httpx>=0.27"]\n\n[tool.uv]\npackage = false\n',
                "app/__init__.py": "",
                "app/agent.py": '"""Harness seam.\n\nSwap the body for agentscope / your own ReAct loop; the MCP tool surface stays\nidentical, so the workbench agent can drive this service either way.\n"""\n\nfrom __future__ import annotations\n\nfrom typing import Any\n\nMODEL = "{{model}}"\n\n\ndef run(prompt: str, tools: dict[str, Any]) -> dict[str, Any]:\n    steps = []\n    if "read_note" in tools:\n        note = tools["read_note"]()\n        steps.append({"tool": "read_note", "result": note})\n    return {"model": MODEL, "prompt": prompt, "steps": steps, "answer": "scaffold agent: wire your harness here"}\n',
                "app/main.py": _AGENT_MAIN + '\n\n\n@app.post("/agent/run")\ndef run_agent(prompt: str) -> dict[str, object]:\n    from app.agent import run\n\n    return run(prompt, {"read_note": read_note, "write_note": write_note})\n',
                ".env.example": "MODEL={{model}}\nPUBLIC_URL={{fqdn_url}}\n",
                "README.md": _README_TMPL,
            },
            notes="工具在 MCP 上，编排在工作台里，harness 可替换。",
        ),
    ]


class BuiltinTemplateSource(TemplateSource):
    def __init__(self) -> None:
        self._specs = {spec.key: spec for spec in _builtin()}

    def list(self) -> list[TemplateSpec]:
        return list(self._specs.values())

    def get(self, key: str) -> TemplateSpec | None:
        return self._specs.get(key)


class DirectoryTemplateSource(TemplateSource):
    """Reads ``template.yaml`` folders. Drop-in extension without touching code."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _load_one(self, folder: Path) -> TemplateSpec | None:
        manifest = folder / "template.yaml"
        if not manifest.exists():
            return None
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        files: dict[str, str] = {}
        files_root = folder / (data.get("files_root") or "files")
        if files_root.exists():
            for path in sorted(files_root.rglob("*")):
                if path.is_file():
                    rel = path.relative_to(files_root).as_posix()
                    try:
                        files[rel] = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
        variables = [
            TemplateVariable(
                key=v["key"], label=v.get("label", v["key"]), default=v.get("default", "")
            )
            for v in data.get("variables", [])
        ]
        return TemplateSpec(
            key=data.get("key", folder.name),
            name=data.get("name", folder.name),
            type=data.get("type", "fullstack"),
            description=data.get("description", ""),
            version=str(data.get("version", "0.0.0")),
            icon=data.get("icon", "box"),
            tags=data.get("tags", []),
            runtime=data.get("runtime", ""),
            run_command=data.get("run_command", ""),
            resources=data.get("resources", ["storage", "database"]),
            variables=variables,
            files=files,
            notes=data.get("notes", ""),
            source=f"dir:{folder.as_posix()}",
        )

    def list(self) -> list[TemplateSpec]:
        if not self.root.exists():
            return []
        specs = []
        for folder in sorted(p for p in self.root.iterdir() if p.is_dir()):
            spec = self._load_one(folder)
            if spec:
                specs.append(spec)
        return specs

    def get(self, key: str) -> TemplateSpec | None:
        for spec in self.list():
            if spec.key == key:
                return spec
        return None


# ---------------------------------------------------------------------------
# facade
# ---------------------------------------------------------------------------


class TemplateRegistry:
    def __init__(self) -> None:
        self.sources: list[TemplateSource] = [BuiltinTemplateSource()]

    def add_source(self, source: TemplateSource) -> None:
        self.sources.append(source)

    def list(self) -> list[TemplateSpec]:
        merged: dict[str, TemplateSpec] = {}
        for source in self.sources:
            for spec in source.list():
                merged.setdefault(spec.key, spec)
        return list(merged.values())

    def get(self, key: str) -> TemplateSpec | None:
        for source in self.sources:
            spec = source.get(key)
            if spec:
                return spec
        return None


def build_registry(extra_dirs: list[Path] | None = None) -> TemplateRegistry:
    registry_ = TemplateRegistry()
    for directory in extra_dirs or []:
        registry_.add_source(DirectoryTemplateSource(directory))
    return registry_
