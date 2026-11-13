"""End-to-end check for the built-in reverse proxy.

Runs a real upstream (HTTP + WebSocket), a real uvicorn listener for the gateway,
and asserts the things that actually matter to a user:

* a published host reaches the project with no port in the URL;
* the preview host reaches the preview port instead;
* the upstream sees a loopback authority and the real host in a header;
* request bodies and streamed responses survive the hop;
* a redirect back to the upstream origin is rewritten to the public host;
* a stopped project answers with the gateway's own page, not a bare 502;
* an unknown host is a 404 that names the suffix;
* a websocket upgrade is proxied both ways (this is what makes HMR work).

Usage::

    cd backend && ./.venv/Scripts/python.exe tools/gateway_smoke.py
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# must be set before app.core.config is imported (it caches at import time)
_TMP = tempfile.mkdtemp(prefix="wb-gateway-smoke-")
os.environ["WB_DATA_DIR"] = _TMP
os.environ["WB_BOOTSTRAP_DEMO_DATA"] = "false"
os.environ["WB_AUTH_ENABLED"] = "false"

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from sqlmodel import Session  # noqa: E402
from websockets.asyncio.client import connect as ws_connect  # noqa: E402
from websockets.sync.server import serve as ws_serve  # noqa: E402

from app.core import config as config_module  # noqa: E402
from app.core.db import engine, init_db  # noqa: E402
from app.models import Project, ProjectStatus  # noqa: E402
from app.services import conventions  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {label}{(' — ' + detail) if detail and not ok else ''}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ---------------------------------------------------------------------------
# upstream: a project that echoes what it received
# ---------------------------------------------------------------------------


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # keep the output readable
        pass

    def _reply(self, status: int, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.send_response(status)
        for key, value in (headers or {"content-type": "text/html; charset=utf-8"}).items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/redirect"):
            self.send_response(302)
            self.send_header("location", f"http://127.0.0.1:{PORT}/after")
            self.send_header("content-length", "0")
            self.end_headers()
            return
        if self.path.startswith("/stream"):
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            for i in range(3):
                chunk = f"data: {i}\n\n".encode()
                self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                self.wfile.flush()
                time.sleep(0.12)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        if self.path.startswith("/echo"):
            payload = {
                "method": self.command,
                "path": self.path,
                "host": self.headers.get("host", ""),
                "xfh": self.headers.get("x-forwarded-host", ""),
                "xfp": self.headers.get("x-forwarded-proto", ""),
                "xfip": self.headers.get("x-forwarded-for", ""),
                "route": self.headers.get("x-workbench-route", ""),
                "cookie": self.headers.get("cookie", ""),
            }
            import json

            self._reply(200, json.dumps(payload).encode(), {"content-type": "application/json"})
            return
        self._reply(200, b"<h1>upstream</h1>")

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        self._reply(200, body, {"content-type": "text/plain"})


PORT = free_port()
upstream = ThreadingHTTPServer(("127.0.0.1", PORT), UpstreamHandler)
threading.Thread(target=upstream.serve_forever, daemon=True).start()

WS_PORT = free_port()


def ws_echo(connection) -> None:
    for message in connection:
        connection.send(message)


ws_server = ws_serve(ws_echo, "127.0.0.1", WS_PORT)
threading.Thread(target=ws_server.serve_forever, daemon=True).start()

# ---------------------------------------------------------------------------
# control-plane data: two projects, production and preview ports
# ---------------------------------------------------------------------------


def seed_projects() -> None:
    init_db()
    with Session(engine) as session:
        session.add(
            Project(
                id="proj_gw_smoke",
                slug="gw-smoke",
                name="Gateway smoke",
                type="fullstack",
                status=ProjectStatus.READY,
                repo_path=os.path.join(_TMP, "gw-smoke"),
                port=PORT,
                dev_port=PORT,
            )
        )
        session.add(
            Project(
                id="proj_gw_ws",
                slug="gw-ws",
                name="Gateway ws",
                type="backend",
                status=ProjectStatus.READY,
                repo_path=os.path.join(_TMP, "gw-ws"),
                port=WS_PORT,
                dev_port=WS_PORT,
            )
        )
        session.commit()


seed_projects()

GATEWAY_PORT = free_port()
os.environ["WB_GATEWAY_HTTP_PORT"] = str(GATEWAY_PORT)
config_module.settings.gateway_http_port = GATEWAY_PORT  # type: ignore[misc]

from app.gateway.proxy import create_app  # noqa: E402  (after the port override)

gateway = create_app()
server = uvicorn.Server(
    uvicorn.Config(gateway, host="127.0.0.1", port=GATEWAY_PORT, log_level="warning")
)
threading.Thread(target=server.run, daemon=True).start()
for _ in range(80):
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", GATEWAY_PORT)) == 0:
            break
    time.sleep(0.1)

BASE = f"http://127.0.0.1:{GATEWAY_PORT}"
PROD_HOST = "gw-smoke.localtest.me"
PREVIEW_HOST = f"gw-smoke{config_module.settings.preview_suffix}.localtest.me"

print(f"\nupstream http :{PORT}  ws :{WS_PORT}  gateway :{GATEWAY_PORT}\n")


# ---------------------------------------------------------------------------
# url shape
# ---------------------------------------------------------------------------


def check_urls() -> None:
    real = config_module.settings
    fake = type(real)(gateway_http_port=80, domain_base_url="")
    conventions.settings = fake  # type: ignore[assignment]
    try:
        project = Project(slug="gw-smoke", name="x", port=4200, dev_port=4204)
        check(
            "port-free production url",
            conventions.production_url(project) == "http://gw-smoke.localtest.me",
            conventions.production_url(project),
        )
        check(
            "port-free preview url",
            conventions.preview_url(project, 4204) == "http://gw-smoke-dev.localtest.me",
            conventions.preview_url(project, 4204),
        )
        fake_nogw = type(real)(gateway_enabled=False, domain_base_url="")
        conventions.settings = fake_nogw  # type: ignore[assignment]
        check(
            "direct url keeps its port when the gateway is off",
            conventions.production_url(project) == "http://gw-smoke.localtest.me:4200",
            conventions.production_url(project),
        )
        fake_tunnel = type(real)(domain_base_url="https://gateway.example.com")
        conventions.settings = fake_tunnel  # type: ignore[assignment]
        check(
            "external proxy base url wins",
            conventions.preview_url(project, 4204) == "https://gateway.example.com",
            conventions.preview_url(project, 4204),
        )
    finally:
        conventions.settings = real  # type: ignore[assignment]


def check_http() -> None:
    with httpx.Client(base_url=BASE, timeout=10) as client:
        response = client.get("/", headers={"host": PROD_HOST})
        check(
            "production host proxies the app",
            response.status_code == 200 and b"upstream" in response.content,
            f"{response.status_code} {response.text[:120]}",
        )
        check(
            "response carries the route marker",
            response.headers.get("x-workbench-route") == "gw-smoke:production",
            str(dict(response.headers)),
        )
        check("no port leaks into the body", f":{PORT}" not in response.text)

        echo = client.get("/echo?x=1", headers={"host": PROD_HOST, "cookie": "sid=abc"}).json()
        check(
            "upstream sees a loopback authority",
            echo["host"] == f"localhost:{PORT}",
            echo["host"],
        )
        check("public host travels in x-forwarded-host", echo["xfh"] == PROD_HOST, echo["xfh"])
        check("scheme is forwarded", echo["xfp"] == "http", echo["xfp"])
        check("route hint reaches the app", echo["route"] == "gw-smoke:production", echo["route"])
        check("query string is preserved", echo["path"] == "/echo?x=1", echo["path"])
        check("cookies pass through", echo["cookie"] == "sid=abc", echo["cookie"])

        posted = client.post("/submit", content=b"payload-123", headers={"host": PROD_HOST})
        check("request bodies are streamed through", posted.text == "payload-123", posted.text)

        stream = client.get("/stream", headers={"host": PROD_HOST})
        check(
            "streamed response arrives intact",
            stream.text.count("data:") == 3,
            stream.text[:120],
        )

        redirect = client.get("/redirect", headers={"host": PROD_HOST}, follow_redirects=False)
        location = redirect.headers.get("location", "")
        expected = conventions.origin_for(PROD_HOST, PORT, via_gateway=True) + "/after"
        check("redirect leaves the loopback origin behind", location == expected, location)

        preview = client.get("/echo", headers={"host": PREVIEW_HOST})
        check(
            "preview host resolves too",
            preview.status_code == 200
            and preview.headers.get("x-workbench-route") == "gw-smoke:preview",
            f"{preview.status_code} {preview.headers.get('x-workbench-route')}",
        )

        unknown = client.get("/", headers={"host": "nobody.localtest.me"})
        check(
            "unknown host explains itself",
            unknown.status_code == 404 and "localtest.me" in unknown.text,
            str(unknown.status_code),
        )

        health = client.get("/__gateway/health", headers={"host": f"localhost:{GATEWAY_PORT}"})
        check(
            "gateway status endpoint",
            health.status_code == 200 and health.json()["routes"] >= 4,
            health.text[:120],
        )


def check_down() -> None:
    """A project whose port nothing listens on: the gateway answers, not the OS."""
    with Session(engine) as session:
        dead = free_port()
        session.add(
            Project(
                id="proj_gw_dead",
                slug="gw-dead",
                name="Gateway dead",
                type="backend",
                status=ProjectStatus.READY,
                repo_path=os.path.join(_TMP, "gw-dead"),
                port=dead,
                dev_port=dead,
            )
        )
        session.commit()
    from app.gateway.routes import table

    table.invalidate()
    with httpx.Client(base_url=BASE, timeout=10) as client:
        response = client.get("/", headers={"host": "gw-dead.localtest.me"})
        check(
            "stopped project gets an explanatory page",
            response.status_code == 502 and "没有在运行" in response.text,
            f"{response.status_code} {response.text[:160]}",
        )


async def check_ws() -> None:
    async with ws_connect(
        f"ws://127.0.0.1:{GATEWAY_PORT}/hmr",
        additional_headers={"host": "gw-ws.localtest.me"},
        proxy=None,
        open_timeout=8,
    ) as socket_:
        await socket_.send("ping-through-gateway")
        reply = await socket_.recv()
    check("websocket upgrade is proxied both ways", reply == "ping-through-gateway", str(reply))


def main() -> int:
    check_urls()
    check_http()
    check_down()
    import asyncio

    asyncio.run(check_ws())

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("failed: " + ", ".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        server.should_exit = True
        upstream.shutdown()
        ws_server.shutdown()
    sys.exit(code)
