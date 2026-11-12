"""Project conventions — "convention over configuration", executable.

Three project types ship with a *spec*, not a config file:

===================  =========================  ==================================
type                 layout                     contract
===================  =========================  ==================================
``fullstack``        backend/ + frontend/       two process trees, one public URL
``frontend``         package.json + index.html  static build, no server secrets
``backend``          app/main.py                HTTP API + optional /mcp mount
===================  =========================  ==================================

Everything a project needs is *derived* from its slug: workspace path, ports,
bucket name, database name, domains, env vars, dev/build/run commands. So a new
project is one row in the database and one directory — zero config files.

When a project genuinely needs to differ, it drops a ``workbench.yaml`` in its
root. That file is the only sanctioned override, and every field in it is
optional; whatever is absent keeps the convention. ``GET /projects/{id}/conventions``
returns the *resolved* spec plus a pass/warn/fail report, so the docs, the UI and
the agent all read the same truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

from app.core.config import settings
from app.models import Project

MANIFEST_NAME = "workbench.yaml"
MAX_SCAN_BYTES = 400_000


# ---------------------------------------------------------------------------
# spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Convention:
    """One line of the contract, phrased so a human and an agent read it the same."""

    key: str
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class TypeSpec:
    type: str
    label: str
    summary: str
    layout: tuple[tuple[str, str], ...]
    dev_command: str
    build_command: str
    run_command: str
    health_path: str
    resources: tuple[str, ...]
    conventions: tuple[Convention, ...] = field(default_factory=tuple)

    def to_public(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "label": self.label,
            "summary": self.summary,
            "layout": [{"path": p, "meaning": m} for p, m in self.layout],
            "commands": {
                "dev": self.dev_command,
                "build": self.build_command,
                "run": self.run_command,
            },
            "health_path": self.health_path,
            "resources": list(self.resources),
            "conventions": [
                {"key": c.key, "title": c.title, "detail": c.detail} for c in self.conventions
            ],
        }


_LAYOUT_DOC = (
    Convention(
        "layout",
        "目录即接口",
        "工作区根目录固定为 data/projects/<slug>，入口文件按 type 约定，不通过配置声明。",
    ),
    Convention(
        "env",
        "环境变量由平台注入",
        "DATA 库/存储/公网地址/端口统一注入 DATABASE_URL、STORAGE_BUCKET、STORAGE_ENDPOINT、PUBLIC_URL、PORT，应用只读不写。",
    ),
    Convention(
        "preview",
        "调试用独立子域",
        "预览环境运行在 <slug>" + settings.preview_suffix + ".<domain>，独立端口与独立进程；生产为 <slug>.<domain>。",
    ),
    Convention(
        "git",
        "每次发布都是一个提交",
        "发布 = git commit + tag deploy/v<N> + 部署该 commit；回滚 = revert 该 commit 后重新发布。",
    ),
)


def _base_conventions() -> tuple[Convention, ...]:
    return _LAYOUT_DOC


FULLSTACK = TypeSpec(
    type="fullstack",
    label="全栈（前后端分离）",
    summary="FastAPI 后端 + 前端单页应用。两个进程，一个入口域名；前端通过 API_BASE 访问后端。",
    layout=(
        ("backend/app/main.py", "后端入口（FastAPI 应用对象 app）"),
        ("backend/pyproject.toml", "后端依赖与 Python 版本"),
        ("frontend/package.json", "前端依赖与 dev/build 脚本"),
        ("frontend/src/", "前端源码根目录"),
        (".env.example", "平台注入变量的说明文件"),
        ("README.md", "入口、命令与资源说明"),
    ),
    dev_command="npm --prefix frontend run dev -- --port {port}",
    build_command="npm --prefix frontend install",
    run_command="npm --prefix frontend run dev",
    health_path="/health",
    resources=("storage", "database"),
    conventions=(
        Convention("split", "前后端分目录", "后端只在 backend/，前端只在 frontend/；共享契约放 docs/ 或 openapi 产物。"),
        Convention("api", "后端必须有 /health", "健康检查返回 200 与 {\"status\": \"ok\"}，部署闸门只认它。"),
        Convention("mcp", "MCP 与 REST 同端口", "后端在 /mcp 挂载 streamable HTTP MCP，不额外开端口。"),
        Convention("cors", "跨域由平台注入", "允许来源从 CORS_ORIGINS 读取，默认包含预览域与生产域。"),
        *(_LAYOUT_DOC[1:]),
    ),
)

FRONTEND = TypeSpec(
    type="frontend",
    label="纯前端（静态站点）",
    summary="无服务端进程，构建产物直接托管；没有数据库，只用对象存储放静态资源。",
    layout=(
        ("package.json", "依赖与 dev/build 脚本"),
        ("index.html", "Vite 入口页面（Next 项目则为 app/page.tsx）"),
        ("src/main.tsx", "应用挂载点"),
        ("src/App.tsx", "根组件"),
        ("README.md", "构建与部署说明"),
    ),
    dev_command="npm run dev -- --port {port}",
    build_command="npm install",
    run_command="npm run dev",
    health_path="/",
    resources=("storage",),
    conventions=(
        Convention("static", "产物即部署物", "构建输出必须是 dist/（或 out/、build/），预览与生产直接托管它。"),
        Convention("nosecret", "前端不含密钥", "任何写进前端代码的 key 都是公开的；需要密钥的调用走后端或 MCP。"),
        Convention("api", "后端地址可配置", "从 API_BASE / VITE_API_BASE 读取，禁止硬编码域名。"),
        *(_LAYOUT_DOC[1:]),
    ),
)

BACKEND = TypeSpec(
    type="backend",
    label="纯后端（API 服务）",
    summary="只有一个 HTTP 服务，既提供 REST，也可在同端口挂 MCP；没有界面。",
    layout=(
        ("app/main.py", "入口（FastAPI 应用对象 app）"),
        ("pyproject.toml", "依赖与 Python 版本"),
        (".env.example", "平台注入变量说明"),
        ("Dockerfile", "可选：容器化部署"),
        ("README.md", "接口清单与调用示例"),
    ),
    dev_command="python -m uvicorn app.main:app --host 0.0.0.0 --port {port} --reload",
    build_command="",
    run_command="python -m uvicorn app.main:app --host 0.0.0.0 --port {port}",
    health_path="/health",
    resources=("storage", "database"),
    conventions=(
        Convention("health", "必须有 /health", "部署闸门与预览探测都请求它，返回 200 才算启动成功。"),
        Convention("mcp", "MCP 与 REST 同端口", "app.mount('/mcp', mcp.http_app(...))，一个进程两种协议。"),
        Convention("openapi", "保留 /docs", "OpenAPI 是 Agent 和调用方的契约来源，不要关闭。"),
        *(_LAYOUT_DOC[1:]),
    ),
)

# ``agent`` projects are backends whose payload happens to be a harness.
_SPECS: dict[str, TypeSpec] = {
    "fullstack": FULLSTACK,
    "frontend": FRONTEND,
    "backend": BACKEND,
    "agent": TypeSpec(
        type="agent",
        label="Agent 服务",
        summary=BACKEND.summary + " 规划循环放在 harness 里，可替换。",
        layout=BACKEND.layout + (("app/agent.py", "harness 接入点"),),
        dev_command=BACKEND.dev_command,
        build_command="",
        run_command=BACKEND.run_command,
        health_path="/health",
        resources=BACKEND.resources,
        conventions=(
            Convention("harness", "harness 可替换", "推理循环与工具面解耦：工具在 MCP 上，循环在 harness 里。"),
            *BACKEND.conventions,
        ),
    ),
}


def spec_for(project_type: str) -> TypeSpec:
    return _SPECS.get(project_type) or FULLSTACK


def specs() -> list[dict[str, Any]]:
    return [spec.to_public() for spec in _SPECS.values()]


# ---------------------------------------------------------------------------
# manifest (the single sanctioned override)
# ---------------------------------------------------------------------------


def manifest_path(project: Project) -> Path:
    return Path(project.repo_path) / MANIFEST_NAME


def load_manifest(project: Project) -> dict[str, Any]:
    path = manifest_path(project)
    if not project.repo_path or not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def preview_suffix(project: Project) -> str:
    override = str(load_manifest(project).get("preview_suffix") or "").strip()
    return override or settings.preview_suffix


def preview_fqdn(project: Project) -> str:
    return f"{project.slug}{preview_suffix(project)}.{settings.domain_suffix}"


def production_fqdn(project: Project) -> str:
    return f"{project.slug}.{settings.domain_suffix}"


def origin_for(fqdn: str, port: int, *, via_gateway: bool | None = None) -> str:
    """Public origin for a host.

    Three deployment shapes, one function:

    * external proxy/tunnel declared — its base URL wins, whatever the ports are;
    * built-in gateway — ``scheme://fqdn`` and the port disappears, because the
      gateway multiplexes every project on one listener and routes by Host;
    * no gateway — ``http://fqdn:port``, the honest *direct* address.

    A non-default gateway port is spelled out rather than hidden: emitting a
    port-free URL for a listener that is not on 80/443 would be a broken link.
    """
    if settings.domain_base_url:
        return settings.domain_base_url.rstrip("/")
    if via_gateway is None:
        via_gateway = settings.gateway_active
    if via_gateway:
        scheme = settings.gateway_scheme
        gateway_port = settings.gateway_port
        default = 443 if scheme == "https" else 80
        return f"{scheme}://{fqdn}" if gateway_port == default else f"{scheme}://{fqdn}:{gateway_port}"
    return f"http://{fqdn}:{port or 0}"


def production_url(project: Project, port: int | None = None) -> str:
    return origin_for(production_fqdn(project), port or project.port or 0)


def preview_url(project: Project, port: int | None) -> str:
    return origin_for(preview_fqdn(project), port or 0)


def owns_host(url: str) -> bool:
    """True when a URL points at a host the platform itself allocated."""
    if not url:
        return False
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return False
    return host == settings.domain_suffix or host.endswith("." + settings.domain_suffix)


def normalize_endpoints(project: Project) -> bool:
    """Re-derive stored public URLs from the current addressing shape.

    A URL is a *stored artifact* here — deployment rows point at it and the UI
    links to it — so flipping the platform between "a port per project" and "one
    gateway listener" would otherwise leave every existing project advertising an
    address that no longer answers. Custom domains are never touched: only hosts
    we handed out ourselves are rewritten.
    """
    changed = False
    expected_preview = preview_url(project, project.dev_port)
    if project.preview_url != expected_preview:
        project.preview_url = expected_preview
        changed = True
    if not project.primary_url or owns_host(project.primary_url):
        expected_production = production_url(project, project.port)
        if project.primary_url != expected_production:
            project.primary_url = expected_production
            changed = True
    return changed


# ---------------------------------------------------------------------------
# resolved spec
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Resolved:
    spec: TypeSpec
    dev_command: str
    build_command: str
    run_command: str
    health_path: str
    overrides: dict[str, str]


def _fmt(template: str, port: int | None, project: Project) -> str:
    """Fill the two placeholders a command may use. Both are derived, never typed."""
    return (
        template.replace("{port}", str(port or 0))
        .replace("{slug}", project.slug)
        .replace("{root}", project.repo_path)
    )


def resolve(project: Project) -> Resolved:
    manifest = load_manifest(project)
    spec = spec_for(str(manifest.get("type") or project.type))
    dev_port = project.dev_port or project.port

    dev = (
        str(manifest.get("dev_command") or "")
        or project.dev_command
        or _fmt(spec.dev_command, dev_port, project)
    )
    build = str(manifest.get("build_command") or "") or project.build_command or spec.build_command
    run = str(manifest.get("run_command") or "") or project.run_command or _fmt(
        spec.run_command, project.port, project
    )
    health = str(manifest.get("health_path") or "") or project.health_path or spec.health_path

    # ``dev_command`` stored at scaffold time already has the concrete dev port baked
    # in; a persisted command must not be re-templated or it would double-render.
    dev = _fmt(dev, dev_port, project) if "{port}" in dev else dev
    build = _fmt(build, project.port, project) if "{port}" in build else build
    run = _fmt(run, project.port, project) if "{port}" in run else run

    overrides = {
        key: str(value)
        for key, value in manifest.items()
        if key in {"type", "dev_command", "build_command", "run_command", "health_path", "preview_suffix"}
    }
    return Resolved(
        spec=spec,
        dev_command=dev,
        build_command=build,
        run_command=run,
        health_path=health,
        overrides=overrides,
    )


# ---------------------------------------------------------------------------
# checks — the report a human, the UI and the agent all consume
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Check:
    key: str
    title: str
    status: str  # pass | warn | fail
    detail: str


def _exists(root: Path, rel: str) -> bool:
    return (root / rel).exists()


def _read(root: Path, rel: str) -> str:
    path = root / rel
    if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _has(root: Path, rel: str, *needles: str) -> bool:
    text = _read(root, rel)
    return bool(text) and all(needle in text for needle in needles)


def _check_common(root: Path) -> list[Check]:
    checks: list[Check] = []
    checks.append(
        Check(
            "readme",
            "README",
            "pass" if _exists(root, "README.md") else "warn",
            "README.md 存在" if _exists(root, "README.md") else "缺少 README.md，接手的人（和 Agent）没有入口信息",
        )
    )
    ignore = _read(root, ".gitignore")
    ok_ignore = "node_modules" in ignore or ".venv" in ignore
    checks.append(
        Check(
            "gitignore",
            "依赖忽略",
            "pass" if ok_ignore else "warn",
            "已排除依赖目录" if ok_ignore else ".gitignore 未排除依赖/产物目录，提交会污染版本历史",
        )
    )
    return checks


def _check_fullstack(root: Path) -> list[Check]:
    return [
        Check(
            "backend_entry",
            "后端入口",
            "pass" if _exists(root, "backend/app/main.py") else "fail",
            "backend/app/main.py" if _exists(root, "backend/app/main.py") else "缺少 backend/app/main.py，无法启动后端",
        ),
        Check(
            "backend_deps",
            "后端依赖",
            "pass" if _exists(root, "backend/pyproject.toml") else "fail",
            "pyproject.toml 存在" if _exists(root, "backend/pyproject.toml") else "缺少 pyproject.toml（约定用 uv 管理）",
        ),
        Check(
            "frontend_entry",
            "前端入口",
            "pass" if _exists(root, "frontend/package.json") else "fail",
            "package.json 存在" if _exists(root, "frontend/package.json") else "缺少 frontend/package.json",
        ),
        Check(
            "frontend_scripts",
            "前端脚本",
            "pass" if _has(root, "frontend/package.json", '"dev"', '"build"') else "warn",
            "dev 与 build 均已声明" if _has(root, "frontend/package.json", '"dev"', '"build"') else "package.json 缺少 dev 或 build 脚本",
        ),
        Check(
            "health",
            "健康检查",
            "pass" if _has(root, "backend/app/main.py", "/health") else "warn",
            "/health 已定义" if _has(root, "backend/app/main.py", "/health") else "未找到 /health，部署健康检查会失败",
        ),
        Check(
            "env_doc",
            "环境变量说明",
            "pass"
            if _has(root, ".env.example", "DATABASE_URL", "STORAGE_BUCKET", "PUBLIC_URL")
            else "warn",
            "三个关键变量均有说明"
            if _has(root, ".env.example", "DATABASE_URL", "STORAGE_BUCKET", "PUBLIC_URL")
            else ".env.example 未完整列出 DATABASE_URL / STORAGE_BUCKET / PUBLIC_URL",
        ),
    ]


def _check_frontend(root: Path) -> list[Check]:
    has_vite = _exists(root, "index.html")
    has_next = _exists(root, "app/page.tsx") or _exists(root, "app/page.jsx")
    return [
        Check(
            "entry",
            "前端入口",
            "pass" if has_vite or has_next else "fail",
            "index.html（Vite）" if has_vite else ("app/page.tsx（Next）" if has_next else "缺少 index.html 或 app/page.tsx"),
        ),
        Check(
            "scripts",
            "构建脚本",
            "pass" if _has(root, "package.json", '"build"') else "fail",
            "build 已声明" if _has(root, "package.json", '"build"') else "没有 build 脚本就没有可部署的产物",
        ),
        Check(
            "source",
            "源码目录",
            "pass" if _exists(root, "src") or _exists(root, "app") else "warn",
            "src/ 或 app/ 存在" if _exists(root, "src") or _exists(root, "app") else "源码目录不符合约定",
        ),
        Check(
            "secret_hygiene",
            ".env 隔离",
            "pass" if ".env" in _read(root, ".gitignore") else "fail",
            ".gitignore 已排除 .env"
            if ".env" in _read(root, ".gitignore")
            else ".env 未忽略：静态站点的环境变量会随产物一起公开",
        ),
        Check(
            "ignore",
            "产物忽略",
            "pass" if "dist" in _read(root, ".gitignore") else "warn",
            "已排除 dist/" if "dist" in _read(root, ".gitignore") else "建议忽略 dist/，产物不进版本库",
        ),
    ]


def _check_backend(root: Path) -> list[Check]:
    entry = "app/main.py" if _exists(root, "app/main.py") else ("main.py" if _exists(root, "main.py") else "")
    text = _read(root, entry) if entry else ""
    return [
        Check(
            "entry",
            "服务入口",
            "pass" if entry else "fail",
            entry or "缺少 app/main.py",
        ),
        Check(
            "deps",
            "依赖清单",
            "pass" if _exists(root, "pyproject.toml") else "fail",
            "pyproject.toml 存在" if _exists(root, "pyproject.toml") else "缺少 pyproject.toml（约定用 uv 管理）",
        ),
        Check(
            "health",
            "健康检查",
            "pass" if "/health" in text else "fail",
            "/health 已定义" if "/health" in text else "没有 /health，部署闸门无法判定启动成功",
        ),
        Check(
            "mcp",
            "MCP 挂载",
            "pass" if "/mcp" in text else "warn",
            "/mcp 已挂载" if "/mcp" in text else "未挂载 MCP；若需要被 Agent 调用，加 app.mount('/mcp', ...)",
        ),
        Check(
            "docker",
            "容器化",
            "pass" if _exists(root, "Dockerfile") else "warn",
            "Dockerfile 存在" if _exists(root, "Dockerfile") else "没有 Dockerfile，容器化部署时需要补",
        ),
    ]


_CHECKERS = {
    "fullstack": _check_fullstack,
    "frontend": _check_frontend,
    "backend": _check_backend,
    "agent": _check_backend,
}


def check(project: Project) -> dict[str, Any]:
    """Resolve the spec for this project and grade the workspace against it."""
    resolved = resolve(project)
    root = Path(project.repo_path)
    checks: list[Check] = []
    if not project.repo_path or not root.is_dir():
        checks.append(Check("workspace", "工作区存在", "fail", "工作区目录不存在，无法校验"))
    else:
        checks.extend(_CHECKERS.get(resolved.spec.type, _check_fullstack)(root))
        checks.extend(_check_common(root))

    counts = {"pass": 0, "warn": 0, "fail": 0}
    for item in checks:
        counts[item.status] = counts.get(item.status, 0) + 1

    manifest = load_manifest(project)
    return {
        "type": resolved.spec.type,
        "label": resolved.spec.label,
        "summary": resolved.spec.summary,
        "spec": resolved.spec.to_public(),
        "commands": {
            "dev": resolved.dev_command,
            "build": resolved.build_command,
            "run": resolved.run_command,
            "health_path": resolved.health_path,
        },
        "preview": {
            "fqdn": preview_fqdn(project),
            "suffix": preview_suffix(project),
            "port": project.dev_port,
            "url": project.preview_url or preview_url(project, project.dev_port),
        },
        "manifest": {
            "path": MANIFEST_NAME,
            "present": manifest_path(project).is_file(),
            "overrides": resolved.overrides,
        },
        "checks": [
            {"key": c.key, "title": c.title, "status": c.status, "detail": c.detail}
            for c in checks
        ],
        "score": counts,
        "conformant": counts["fail"] == 0,
    }
