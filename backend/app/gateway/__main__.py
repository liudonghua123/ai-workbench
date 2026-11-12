"""``python -m app.gateway [--port 80]`` — run the reverse proxy.

A separate process on purpose: it must keep serving published apps while the
control plane is being restarted or upgraded, and it is the only thing that needs
a privileged port. Nothing about it is stateful — route data comes from the same
database the control plane writes, and certificates come from files.

With TLS enabled it runs *two* listeners: 443 with SNI-selected certificates, and
80 for the things that only work over plain HTTP — the ACME http-01 challenge, and
a 308 redirect for everything else. Both share one route table and one process
because the challenge and the certificate it produces must see the same state.

Sockets are bound here rather than inside uvicorn so a privileged port that cannot
be taken (no root, or Windows reserving 80 for http.sys) degrades to "no redirect"
instead of killing the HTTPS listener with it.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
import socket
import sys
from dataclasses import dataclass

import uvicorn

from app.core.config import settings


@dataclass(slots=True)
class Listener:
    server: uvicorn.Server
    sock: socket.socket
    port: int
    tls: bool


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.gateway",
        description="AI Workbench 内置反向代理：按 Host 把项目域名转发到本机端口。",
    )
    parser.add_argument(
        "--host", default=settings.gateway_bind_host, help="监听地址（默认 0.0.0.0）"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"主监听端口（HTTP {settings.gateway_http_port} / TLS {settings.gateway_https_port}）",
    )
    parser.add_argument(
        "--tls-cert", default=settings.gateway_tls_cert, help="通配符证书链路径（优先）"
    )
    parser.add_argument("--tls-key", default=settings.gateway_tls_key, help="证书私钥路径")
    parser.add_argument(
        "--no-tls",
        action="store_true",
        help="即使配置了证书也只监听 HTTP（例如前面已有 TLS 终止）",
    )
    parser.add_argument(
        "--acme-poll",
        type=float,
        default=settings.gateway_acme_interval,
        help="多久检查一次证书过期与待签发请求（秒）",
    )
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# listeners
# ---------------------------------------------------------------------------


class _Server(uvicorn.Server):
    """A uvicorn server that does not fight the others over signal handlers."""

    def install_signal_handlers(self) -> None:  # noqa: D102 - documented above
        return None


def _bind(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.setblocking(False)
    return sock


def _config(app, port: int, args: argparse.Namespace, **extra) -> uvicorn.Config:
    return uvicorn.Config(
        app,
        host=args.host,
        port=port,
        log_level=args.log_level,
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
        access_log=False,
        lifespan="on",
        **extra,
    )


def _build(args: argparse.Namespace) -> tuple[list[Listener], list[str], tuple | None]:
    from app.core.config import settings as conf
    from app.gateway.acme import IssueQueue, Renewer
    from app.gateway.proxy import create_app
    from app.gateway.tls import CertStore

    tls_wanted = bool(conf.gateway_tls_enabled and not args.no_tls)
    notes: list[str] = []
    listeners: list[Listener] = []

    if not tls_wanted:
        port = args.port or conf.gateway_http_port
        listeners.append(
            Listener(
                server=_Server(_config(create_app(), port, args)),
                sock=_bind(args.host, port),
                port=port,
                tls=False,
            )
        )
        return listeners, notes, None

    store = CertStore(conf.tls_dir, cert_file=args.tls_cert, key_file=args.tls_key)
    https_port = args.port or conf.gateway_https_port
    challenge = _challenge_store(conf.tls_dir)

    def factory(_config_obj, _default):  # noqa: ANN001 - uvicorn signature
        return store.server_context()

    listeners.append(
        Listener(
            server=_Server(_config(create_app(), https_port, args, ssl_context_factory=factory)),
            sock=_bind(args.host, https_port),
            port=https_port,
            tls=True,
        )
    )

    redirect = conf.gateway_https_redirect and https_port != conf.gateway_http_port
    if redirect or conf.gateway_acme_enabled:
        port_80 = conf.gateway_http_port
        if port_80 != https_port:
            try:
                sock = _bind(args.host, port_80)
            except OSError as exc:
                notes.append(
                    f"端口 {port_80} 未能监听（{exc.strerror or exc}）："
                    "http-01 验证与 https 跳转不可用。需要 root/CAP_NET_BIND_SERVICE，"
                    "或换端口后让前面的代理回源。"
                )
            else:
                listeners.append(
                    Listener(
                        server=_Server(
                            _config(
                                create_app(challenges=challenge, redirect_https=redirect),
                                port_80,
                                args,
                            )
                        ),
                        sock=sock,
                        port=port_80,
                        tls=False,
                    )
                )

    renewer = None
    if conf.gateway_acme_enabled:
        renewer = Renewer(
            store,
            IssueQueue(conf.tls_dir / "queue"),
            directory_url=conf.gateway_acme_directory,
            email=conf.gateway_acme_email,
            renew_days=conf.gateway_acme_renew_days,
            interval=args.acme_poll,
        )
    return listeners, notes, (store, renewer)


def _challenge_store(tls_dir):  # noqa: ANN001, ANN202 - keeps tls import lazy in --no-tls
    from app.gateway.acme import Challenge

    return Challenge(tls_dir / "challenges")


def _banner(listeners: list[Listener], notes: list[str], info: tuple | None) -> None:
    routes = getattr(listeners[0].server.config.app, "routes", None) if listeners else None
    snapshot = routes.snapshot() if routes is not None else []
    print(
        f"gateway  {settings.gateway_scheme}  tls={settings.gateway_tls_mode}  "
        f"routes={len(snapshot)}  suffix={settings.domain_suffix}  "
        f"preview={settings.preview_suffix}",
        flush=True,
    )
    if info is not None:
        store, renewer = info
        primary = store.default()
        if primary is not None:
            hosts = ", ".join(primary.hosts[:4]) or "—"
            print(
                f"  certificate  {hosts}  issuer={primary.issuer}  "
                f"{primary.days_left}d left  key_ok={primary.key_ok}",
                flush=True,
            )
        else:
            print(
                "  certificate  (none yet — serving a bootstrap self-signed certificate)\n"
                "               issue one with: python -m app.gateway acme --all",
                flush=True,
            )
        if renewer is not None:
            print(
                f"  acme         {settings.gateway_acme_directory}  "
                f"renew<{settings.gateway_acme_renew_days}d  "
                f"email={settings.gateway_acme_email or '—'}",
                flush=True,
            )
    for listener in listeners:
        kind = "https" if listener.tls else "http"
        print(f"  listening    {listener.server.config.host}:{listener.port} ({kind})", flush=True)
    for note in notes:
        print(f"  warning      {note}", flush=True)
    for route in snapshot[:40]:
        print(f"  {route.host:<44} -> {route.upstream}  ({route.environment})", flush=True)
    if len(snapshot) > 40:
        print(f"  … {len(snapshot) - 40} more", flush=True)
    if listeners:
        print(
            f"  status page  {settings.gateway_scheme}://localhost:{listeners[0].port}/__gateway/",
            flush=True,
        )


async def _serve(listeners: list[Listener], renewer) -> None:  # noqa: ANN001
    loop = asyncio.get_running_loop()

    def stop(*_args) -> None:
        for listener in listeners:
            listener.server.should_exit = True

    for name in ("SIGINT", "SIGTERM"):
        with contextlib.suppress(AttributeError, NotImplementedError, RuntimeError, ValueError):
            loop.add_signal_handler(getattr(signal, name), stop)

    tasks = [
        asyncio.create_task(listener.server.serve(sockets=[listener.sock]))
        for listener in listeners
    ]
    if renewer is not None:
        tasks.append(asyncio.create_task(renewer.run()))
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(BaseException):
                await task
        for listener in listeners:
            with contextlib.suppress(OSError):
                listener.sock.close()


# ---------------------------------------------------------------------------
# acme subcommands
# ---------------------------------------------------------------------------


def _acme_cli(argv: list[str]) -> int:
    from app.core.config import settings as conf
    from app.gateway.acme import IssueQueue, Renewer
    from app.gateway.tls import CertStore

    parser = argparse.ArgumentParser(
        prog="python -m app.gateway acme",
        description="用 http-01 申请证书（通配符请改用 --tls-cert/--tls-key）。",
    )
    parser.add_argument("--host", action="append", default=[], help="要签发的域名，可重复")
    parser.add_argument("--all", action="store_true", help="为所有缺失/即将过期的项目域名签发")
    parser.add_argument("--status", action="store_true", help="只打印证书状态")
    parser.add_argument("--force", action="store_true", help="即使证书仍有效也重新签发")
    args = parser.parse_args(argv)

    store = CertStore(conf.tls_dir, cert_file=conf.gateway_tls_cert, key_file=conf.gateway_tls_key)
    queue = IssueQueue(conf.tls_dir / "queue")

    if args.status or not (args.host or args.all):
        status = store.status()
        print(
            f"tls={conf.gateway_tls_mode}  dir={status['directory']}  "
            f"certs={status['count']}  expiring={status['expiring']}",
            flush=True,
        )
        for cert in status["certs"]:  # type: ignore[union-attr]
            hosts = ", ".join(cert["hosts"]) or cert["path"]
            print(
                f"  {hosts:<46} {cert['source']:<8} {cert['issuer'][:26]:<26} {cert['days_left']}d",
                flush=True,
            )
        for row in queue.listing():
            print(f"  queue {row['host']:<38} {row['state']:<8} {row['message'][:60]}", flush=True)
        return 0

    if not conf.gateway_acme_enabled:
        print(
            "自动签发未启用：设置 WB_GATEWAY_ACME_ENABLED=true"
            "（可配 WB_GATEWAY_ACME_EMAIL、WB_GATEWAY_ACME_DIRECTORY）。",
            file=sys.stderr,
        )
        return 2

    renewer = Renewer(
        store,
        queue,
        directory_url=conf.gateway_acme_directory,
        email=conf.gateway_acme_email,
        renew_days=0 if args.force else conf.gateway_acme_renew_days,
    )

    async def run() -> int:
        targets = list(dict.fromkeys(args.host))
        if args.all:
            targets = list(dict.fromkeys(targets + renewer._stale_hosts()))
        if not targets:
            print("没有需要签发的域名。", flush=True)
            return 0
        failed = 0
        for host in targets:
            result = await renewer.issue(host, reason="cli")
            mark = "ok  " if result["ok"] else "fail"
            print(f"  [{mark}] {host}  {result['message']}", flush=True)
            failed += 0 if result["ok"] else 1
        return 1 if failed else 0

    return asyncio.run(run())


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "acme":
        return _acme_cli(argv[1:])

    args = _parse(argv)
    try:
        listeners, notes, info = _build(args)
    except OSError as exc:
        print(
            f"\n无法监听 {args.host}:{exc}\n"
            "  · 80/443 在 Linux/macOS 需要 root 或 CAP_NET_BIND_SERVICE\n"
            "  · Windows 上如果端口被 http.sys 保留，先 `netsh http show urlacl`\n"
            "  · 或改用非特权端口：WB_GATEWAY_HTTP_PORT=8080 python -m app.gateway\n"
            "    （此时 URL 会带端口，因为无端口的地址会指向错误的监听）",
            file=sys.stderr,
        )
        return 2

    _banner(listeners, notes, info)
    renewer = info[1] if info is not None else None
    try:
        asyncio.run(_serve(listeners, renewer))
    except KeyboardInterrupt:  # pragma: no cover - interactive Ctrl+C
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
