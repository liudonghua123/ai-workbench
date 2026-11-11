"""Static preview host — the preview environment's floor, not its ceiling.

A preview must be *reachable* the moment you ask for it. Spawning a dev server
requires installed dependencies, which a freshly scaffolded project does not have,
and a failed ``npm run dev`` would leave the debug pane pointing at a dead port.

So the local deploy provider degrades in one direction only: real dev server when it
can, otherwise this stdlib host serves

1. ``dist/`` (or ``build/`` / ``out/``) when a build exists,
2. the workspace root when ``index.html`` is already runnable,
3. a generated explainer page (why the preview cannot run yet + the file tree +
   README + the exact command to fix it).

The URL therefore always renders something honest, and the log says which of the
three it is.
"""

from __future__ import annotations

import html
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BUILD_DIRS = ("dist", "build", "out", "public")
_JSX_MARKERS = (".tsx", ".jsx", ".vue", ".svelte")


class _QuietHandler(SimpleHTTPRequestHandler):
    """No access log noise in the deployment stream."""

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003
        return

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Workbench-Preview", "1")
        super().end_headers()


def _looks_compiled(root: Path) -> bool:
    index = root / "index.html"
    if not index.is_file():
        return False
    try:
        text = index.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return not any(marker in text for marker in _JSX_MARKERS)


def _tree(root: Path, limit: int = 60) -> list[str]:
    skip = {"node_modules", ".git", ".venv", "__pycache__", ".next", "dist"}
    paths: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in skip for part in rel.parts):
            continue
        if len(paths) >= limit:
            break
        paths.append(rel.as_posix() + ("/" if path.is_dir() else ""))
    return paths


def explainer_page(
    *,
    title: str,
    slug: str,
    project_type: str,
    reason: str,
    dev_command: str,
    health_path: str,
    root: Path,
) -> str:
    """A real page, not an error: what is missing and what to run next."""
    readme = ""
    readme_path = root / "README.md"
    if readme_path.is_file():
        try:
            readme = readme_path.read_text(encoding="utf-8", errors="replace")[:4000]
        except OSError:
            readme = ""

    files = "".join(f"<li>{html.escape(item)}</li>" for item in _tree(root))
    readme_block = (
        f"<pre class='readme'>{html.escape(readme)}</pre>" if readme else "<p class='muted'>工作区没有 README.md</p>"
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{html.escape(title)} · 预览</title>
<style>
  :root {{
    color-scheme: light dark;
    --bg: #f7f6f2; --fg: #16161c; --muted: #6b6b78; --card: #fff; --line: #e3e0d8;
    --accent: #4040c8; --code: #f1efe9;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #0d0d12; --fg: #eceaf4; --muted: #9b9aaa; --card: #15151d; --line: #272732;
             --accent: #8b8bff; --code: #1b1b24; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--fg);
         font: 14px/1.65 ui-sans-serif, system-ui, -apple-system, "Segoe UI", "PingFang SC", sans-serif; }}
  .wrap {{ max-width: 900px; margin: 0 auto; padding: 44px 24px 72px; }}
  .eyebrow {{ font-size: 10.5px; letter-spacing: .2em; text-transform: uppercase; color: var(--muted); }}
  h1 {{ font-size: 27px; margin: 8px 0 6px; letter-spacing: -.02em; }}
  .slug {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); font-size: 12px; }}
  .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 18px 20px; margin-top: 18px; }}
  .badge {{ display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 99px;
            border: 1px solid var(--line); color: var(--muted); }}
  .reason {{ margin: 10px 0 0; color: var(--fg); }}
  code, pre {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  code {{ background: var(--code); padding: 1px 5px; border-radius: 5px; font-size: 12.5px; }}
  .cmd {{ display: block; background: var(--code); border-radius: 10px; padding: 11px 13px; margin-top: 10px;
          font-size: 12.5px; overflow-x: auto; }}
  h2 {{ font-size: 13px; text-transform: uppercase; letter-spacing: .1em; color: var(--muted); margin: 0 0 10px; }}
  ul.files {{ list-style: none; margin: 0; padding: 0; columns: 2; font-family: ui-monospace, Menlo, monospace;
              font-size: 12px; color: var(--muted); }}
  ul.files li {{ padding: 1px 0; break-inside: avoid; }}
  .readme {{ white-space: pre-wrap; font-size: 12px; color: var(--muted); margin: 0; max-height: 280px; overflow: auto; }}
  .muted {{ color: var(--muted); }}
  .grid {{ display: grid; grid-template-columns: minmax(0,1fr) minmax(0,1fr); gap: 18px; }}
  @media (max-width: 720px) {{ .grid {{ grid-template-columns: 1fr; }} ul.files {{ columns: 1; }} }}
</style>
</head>
<body>
  <div class="wrap">
    <p class="eyebrow">Workbench preview · {html.escape(project_type)}</p>
    <h1>{html.escape(title)}</h1>
    <p class="slug">{html.escape(slug)} · health <code>{html.escape(health_path)}</code></p>

    <div class="card">
      <span class="badge">静态预览宿主</span>
      <p class="reason">{html.escape(reason)}</p>
      <span class="cmd">$ {html.escape(dev_command or "（未配置 dev_command）")}</span>
      <p class="muted" style="margin:12px 0 0;font-size:12px">
        在安装了依赖的宿主机上，预览会自动切到真实 dev server；这里只是没有依赖时的兜底。
      </p>
    </div>

    <div class="grid">
      <div class="card">
        <h2>工作区文件</h2>
        <ul class="files">{files or "<li class='muted'>空工作区</li>"}</ul>
      </div>
      <div class="card">
        <h2>README</h2>
        {readme_block}
      </div>
    </div>
  </div>
</body>
</html>
"""


class StaticPreviewHost:
    """Serves one project directory on one port, in a background thread."""

    def __init__(
        self,
        *,
        root: Path,
        port: int,
        title: str,
        slug: str,
        project_type: str,
        dev_command: str,
        health_path: str,
        reason: str,
    ) -> None:
        self.root = root
        self.port = port
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.mode = ""
        self.serve_dir = root
        self._title = title
        self._slug = slug
        self._type = project_type
        self._dev_command = dev_command
        self._health = health_path
        self._reason = reason

    def _pick_dir(self) -> tuple[Path, str]:
        for name in BUILD_DIRS:
            candidate = self.root / name
            if (candidate / "index.html").is_file():
                return candidate, f"static:{name}"
        if _looks_compiled(self.root):
            return self.root, "static:workspace"
        return _write_explainer(
            root=self.root,
            title=self._title,
            slug=self._slug,
            project_type=self._type,
            reason=self._reason,
            dev_command=self._dev_command,
            health_path=self._health,
        )

    def start(self) -> dict[str, str]:
        serve_dir, mode = self._pick_dir()
        handler = partial(_QuietHandler, directory=str(serve_dir))
        try:
            self.server = ThreadingHTTPServer(("0.0.0.0", self.port), handler)
        except OSError as exc:
            raise RuntimeError(f"端口 {self.port} 无法绑定（{exc}）") from exc
        self.server.daemon_threads = True
        self.serve_dir = serve_dir
        self.mode = mode
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True
        )
        self.thread.start()
        return {"mode": mode, "root": str(serve_dir)}

    def stop(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        if self.thread is not None:
            self.thread.join(timeout=3)
            self.thread = None

    def info(self) -> dict[str, object]:
        return {"mode": self.mode, "root": str(self.serve_dir), "port": self.port}


def _write_explainer(
    *,
    root: Path,
    title: str,
    slug: str,
    project_type: str,
    reason: str,
    dev_command: str,
    health_path: str,
) -> tuple[Path, str]:
    from app.core.config import settings

    target = settings.logs_dir / f"preview-{slug}"
    target.mkdir(parents=True, exist_ok=True)
    page = explainer_page(
        title=title,
        slug=slug,
        project_type=project_type,
        reason=reason,
        dev_command=dev_command,
        health_path=health_path,
        root=root,
    )
    (target / "index.html").write_text(page, encoding="utf-8")
    return target, "static:explainer"
