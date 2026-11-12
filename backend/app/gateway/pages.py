"""HTML the gateway renders itself — the only pages that never reach a project.

A reverse proxy that answers ``502 Bad Gateway`` with an empty body is a bad
first impression: the person looking at it is usually the author of the app and
needs to know *which* environment is down and what to do about it. These pages
are small, self-contained (no external assets, no script) and readable in both
colour schemes.
"""

from __future__ import annotations

from html import escape

from app.core.config import settings

_CSS = """
:root {
  --bg: #f6f7f9; --panel: #ffffff; --ink: #16181d; --ink-2: #5b6270; --ink-3: #8b93a1;
  --line: #e3e6ec; --accent: #2f6fed; --warn: #b45309; --warn-bg: #fff7ed;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0d0f13; --panel: #15181e; --ink: #eef1f6; --ink-2: #a3abb9; --ink-3: #6e7684;
    --line: #242932; --accent: #6c9bff; --warn: #fbbf24; --warn-bg: #241d10;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 32px 20px;
  background: var(--bg); color: var(--ink);
  font: 400 14px/1.6 ui-sans-serif, system-ui, -apple-system, "Segoe UI", "PingFang SC",
    "Microsoft YaHei", sans-serif;
  -webkit-font-smoothing: antialiased;
}
.wrap { width: 100%; max-width: 620px; }
.brand { display: flex; align-items: center; gap: 8px; margin-bottom: 18px; color: var(--ink-3); font-size: 12px; }
.brand b { color: var(--ink-2); font-weight: 600; letter-spacing: .02em; }
.dot { width: 7px; height: 7px; border-radius: 999px; background: var(--accent); }
.dot.warn { background: var(--warn); }
.panel { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; overflow: hidden; }
.head { padding: 22px 22px 0; }
.code { font: 600 12px/1 var(--mono); letter-spacing: .12em; color: var(--warn); text-transform: uppercase; }
h1 { margin: 8px 0 6px; font-size: 21px; line-height: 1.35; letter-spacing: -.01em; }
.lead { margin: 0; color: var(--ink-2); font-size: 13.5px; }
.body { padding: 18px 22px 22px; }
.facts { display: grid; gap: 0; margin: 4px 0 18px; border-top: 1px solid var(--line); }
.fact { display: flex; gap: 12px; padding: 9px 0; border-bottom: 1px solid var(--line); font-size: 12.5px; }
.fact span:first-child { flex: 0 0 92px; color: var(--ink-3); }
.fact span:last-child { font-family: var(--mono); word-break: break-all; color: var(--ink); }
.hint { margin: 0 0 18px; padding: 12px 14px; border-radius: 10px; background: var(--warn-bg); color: var(--warn); font-size: 12.5px; }
.hint code { font-family: var(--mono); }
.actions { display: flex; flex-wrap: wrap; gap: 8px; }
a.btn { display: inline-flex; align-items: center; gap: 6px; height: 32px; padding: 0 13px; border-radius: 8px;
  border: 1px solid var(--line); color: var(--ink); text-decoration: none; font-size: 12.5px; font-weight: 500; }
a.btn:hover { border-color: var(--ink-3); }
a.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { text-align: left; font-weight: 500; color: var(--ink-3); padding: 8px 0; border-bottom: 1px solid var(--line); font-size: 11px; letter-spacing: .06em; text-transform: uppercase; }
td { padding: 9px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
td.host { font-family: var(--mono); word-break: break-all; }
td.host a { color: var(--accent); text-decoration: none; }
.env { font: 600 10px/1 var(--mono); padding: 4px 6px; border-radius: 5px; letter-spacing: .04em; text-transform: uppercase; }
.env.preview { background: var(--warn-bg); color: var(--warn); }
.env.production { background: color-mix(in srgb, var(--accent) 12%, transparent); color: var(--accent); }
.empty { padding: 26px 0; text-align: center; color: var(--ink-3); font-size: 13px; }
.foot { margin-top: 14px; color: var(--ink-3); font-size: 11.5px; }
"""


def _document(title: str, body: str, *, tone: str = "") -> bytes:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{escape(title)}</title><style>{_CSS}</style></head><body>"
        f"<div class=\"wrap\">{body}</div></body></html>"
    ).encode()


def _esc(value: object) -> str:
    return escape(str(value), quote=True)


def _brand(tone: str, host: str) -> str:
    """``AI Workbench 网关 · <host>`` — the one line that says who answered."""
    where = ""
    if host:
        where = f' · <span style="font-family:var(--mono)">{_esc(host)}</span>'
    return (
        f'<div class="brand"><i class="dot {tone}"></i>'
        f"<b>AI Workbench</b> 网关{where}</div>"
    )


def error_page(
    *,
    code: int,
    heading: str,
    lead: str,
    host: str = "",
    rows: list[tuple[str, str]] | None = None,
    hint: str = "",
    actions: list[tuple[str, str]] | None = None,
) -> bytes:
    facts = "".join(
        f'<div class="fact"><span>{_esc(k)}</span><span>{_esc(v)}</span></div>'
        for k, v in (rows or [])
        if v
    )
    buttons = "".join(
        f'<a class="btn{" primary" if i == 0 else ""}" href="{_esc(href)}">{_esc(label)}</a>'
        for i, (label, href) in enumerate(actions or [])
    )
    body = "".join(
        [
            _brand("warn", host),
            '<div class="panel"><div class="head">',
            f'<div class="code">HTTP {code}</div>',
            f'<h1>{_esc(heading)}</h1><p class="lead">{_esc(lead)}</p></div>',
            '<div class="body">',
            f'<div class="facts">{facts}</div>' if facts else "",
            f'<p class="hint">{hint}</p>' if hint else "",
            f'<div class="actions">{buttons}</div></div></div>',
            '<div class="foot">由内置反向代理直接生成 · 这不是项目的响应</div>',
        ]
    )
    return _document(f"{code} · {heading}", body)


def not_running(host: str, slug: str, environment: str, port: int, retry: str = "") -> bytes:
    preview = environment == "preview"
    console = f"{settings.frontend_url.rstrip('/')}/projects"
    return error_page(
        code=502,
        heading=f"{slug} 的{('预览' if preview else '生产')}环境没有在运行",
        lead=(
            "网关已经把这个域名路由到了项目，但上游端口没有监听。"
            + ("预览只在启动后存活，控制面重启会带走它的进程。" if preview else "先部署一次，或确认进程仍在运行。")
        ),
        host=host,
        rows=[
            ("项目", slug),
            ("环境", "preview" if preview else "production"),
            ("上游", f"{settings.gateway_upstream_host}:{port}"),
        ],
        hint=(
            "在工作台里点「启动预览」即可；预览端口每次启动都会重新拉起 dev server。"
            if preview
            else "在工作台里点「部署」，或检查 run_command 的启动日志。"
        ),
        actions=[("打开工作台", console)] + ([("重试", retry)] if retry else []),
    )


def no_route(host: str) -> bytes:
    examples = f"<项目名>.{settings.domain_suffix} 和 <项目名>{settings.preview_suffix}.{settings.domain_suffix}"
    return error_page(
        code=404,
        heading="这个域名没有绑定任何项目",
        lead=f"网关认识 {examples}，但这个 Host 不在路由表里。",
        host=host,
        rows=[("域名后缀", settings.domain_suffix), ("预览后缀", settings.preview_suffix)],
        hint="新建项目时会自动分配域名；自定义域名需要先在项目的「设置」里绑定。",
        actions=[("打开工作台", f"{settings.frontend_url.rstrip('/')}/projects")],
    )


def bad_upstream(host: str, slug: str, port: int, error: str, retry: str = "") -> bytes:
    return error_page(
        code=502,
        heading="上游没有响应",
        lead="网关连上了路由，但转发请求时上游拒绝了连接。",
        host=host,
        rows=[("项目", slug), ("上游", f"{settings.gateway_upstream_host}:{port}"), ("错误", error)],
        actions=[("重试", retry)] if retry else [],
    )


def status_page(routes: list[dict[str, object]], *, host: str) -> bytes:
    rows = ""
    for route in routes:
        env = str(route["environment"])
        rows += (
            "<tr>"
            f'<td class="host"><a href="{_esc(route["url"])}">{_esc(route["host"])}</a></td>'
            f'<td>{_esc(route["slug"])}</td>'
            f'<td><span class="env {_esc(env)}">{_esc(env)}</span></td>'
            f'<td class="host">{_esc(route["upstream"])}</td>'
            "</tr>"
        )
    table = (
        "<table><thead><tr><th>域名</th><th>项目</th><th>环境</th><th>上游</th></tr></thead>"
        f"<tbody>{rows}</tbody></table>"
        if rows
        else '<div class="empty">路由表是空的，先去工作台新建一个项目。</div>'
    )
    suffix = _esc(settings.domain_suffix)
    preview_example = f"&lt;项目&gt;{_esc(settings.preview_suffix)}.{suffix}"
    body = "".join(
        [
            _brand("", host),
            '<div class="panel"><div class="head"><div class="code">Gateway</div>',
            "<h1>反向代理正在运行</h1>",
            f'<p class="lead">按 Host 路由到 {_esc(settings.gateway_upstream_host)} 上的项目端口，'
            f"正式域名 <code>&lt;项目&gt;.{suffix}</code>，"
            f"预览 <code>{preview_example}</code>。</p></div>",
            f'<div class="body">{table}</div></div>',
            '<div class="foot">这是网关自己的状态页；项目的域名不会看到它。</div>',
        ]
    )
    return _document("Gateway · AI Workbench", body)
