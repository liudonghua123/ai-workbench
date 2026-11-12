"""``python -m app.gateway [--port 80]`` — run the reverse proxy.

A separate process on purpose: it must keep serving published apps while the
control plane is being restarted or upgraded, and it is the only thing that needs
a privileged port. Nothing about it is stateful — route data comes from the same
database the control plane writes.
"""

from __future__ import annotations

import argparse
import sys

import uvicorn

from app.core.config import settings
from app.gateway.proxy import create_app


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
        help=f"监听端口（默认 {settings.gateway_http_port} / TLS 时 {settings.gateway_https_port}）",
    )
    parser.add_argument("--tls-cert", default=settings.gateway_tls_cert, help="TLS 证书路径")
    parser.add_argument("--tls-key", default=settings.gateway_tls_key, help="TLS 私钥路径")
    parser.add_argument("--log-level", default="info")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    tls = bool(args.tls_cert and args.tls_key)
    port = args.port or (settings.gateway_https_port if tls else settings.gateway_http_port)
    scheme = "https" if tls else "http"

    app = create_app()
    routes = app.routes.snapshot()
    print(
        f"gateway  {scheme}://{args.host}:{port}  "
        f"routes={len(routes)}  suffix={settings.domain_suffix}  "
        f"preview={settings.preview_suffix}",
        flush=True,
    )
    for route in routes[:40]:
        print(f"  {route.host:<44} -> {route.upstream}  ({route.environment})", flush=True)
    if len(routes) > 40:
        print(f"  … {len(routes) - 40} more", flush=True)
    print(f"  status page: {scheme}://localhost:{port}/__gateway/", flush=True)

    try:
        uvicorn.run(
            app,
            host=args.host,
            port=port,
            log_level=args.log_level,
            server_header=False,
            proxy_headers=True,
            forwarded_allow_ips="*",
            ssl_certfile=args.tls_cert or None,
            ssl_keyfile=args.tls_key or None,
        )
    except OSError as exc:
        print(
            f"\n无法监听 {args.host}:{port}：{exc}\n"
            "  · 80/443 在 Linux/macOS 需要 root 或 CAP_NET_BIND_SERVICE\n"
            "  · Windows 上如果端口被 http.sys 保留，先 `netsh http show urlacl`\n"
            f"  · 或改用非特权端口：WB_GATEWAY_HTTP_PORT=8080 python -m app.gateway\n"
            "    （此时 URL 会带端口，因为无端口的地址会指向错误的监听）",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
