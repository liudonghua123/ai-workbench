"""The reverse proxy itself: one listener, every project, routed by Host.

Shape
-----
``<slug>.<suffix>``       -> 127.0.0.1:<project.port>      (production)
``<slug><preview>.<suffix>`` -> 127.0.0.1:<project.dev_port>  (preview)

Both are the *same* socket, which is why a published app has no port in its URL.
The listener is a plain ASGI app, so it runs under uvicorn like anything else and
can be fronted by a TLS terminator (or ``cloudflared``) without changes.

Three decisions worth knowing about
-----------------------------------
*Body streaming, not buffering.* Both directions are pumped chunk by chunk, so
SSE, log tails and large downloads behave as they do upstream. A proxy that
buffers would silently break every streaming endpoint in the product.

*Upstream ``Host`` is ``localhost:<port>``.* Dev servers validate the Host header
(Vite blocks unknown hosts outright, Next warns). Forwarding the public host
would make previews fail with a 403 that looks like an auth bug, so the public
host travels in ``X-Forwarded-Host`` instead — exactly what a careful nginx
config does in front of a dev server.

*No request data influences the upstream.* Host is matched against a table built
from the database; the port comes from the project row. There is no path that
turns the gateway into an open proxy or lets a Host header reach an arbitrary
local port.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from app.core.config import settings
from app.gateway import pages
from app.gateway.routes import Route, RouteTable, normalize_host
from app.gateway.routes import table as default_table
from app.services import conventions

logger = logging.getLogger("workbench.gateway")

HOP_BY_HOP = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailers",
    b"transfer-encoding",
    b"upgrade",
}

# headers that describe the *client's* connection, not the request body; they are
# rebuilt for the upstream hop instead of being copied
REWRITTEN = {b"host", b"content-length", b"x-forwarded-for", b"x-forwarded-host",
             b"x-forwarded-proto", b"x-forwarded-port", b"x-real-ip", b"forwarded"}

CONSOLE_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"}

HEALTH_PATH = "/__gateway/health"
STATUS_PATH = "/__gateway/"


class Gateway:
    """ASGI application. One instance per process; holds the HTTP client pool."""

    def __init__(self, routes: RouteTable | None = None) -> None:
        self.routes = routes or default_table
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle -------------------------------------------------------
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                # read=None keeps long-lived streams (SSE) open indefinitely
                timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
                limits=httpx.Limits(max_connections=200, max_keepalive_connections=60),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __call__(self, scope: dict, receive, send) -> None:
        kind = scope.get("type")
        if kind == "lifespan":
            await self._lifespan(receive, send)
            return
        if kind == "websocket":
            await self._websocket(scope, receive, send)
            return
        if kind != "http":
            return
        await self._http_request(scope, receive, send)

    async def _lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                self.routes.invalidate()
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return

    # -- console (the gateway talking about itself) ----------------------
    def _is_console(self, host: str) -> bool:
        return host in CONSOLE_HOSTS or host == normalize_host(settings.domain_suffix)

    def _console_response(self, path: str, host: str) -> tuple[int, bytes, str]:
        routes = self.routes.snapshot()
        if path.rstrip("/") == HEALTH_PATH.rstrip("/"):
            payload = {
                "status": "ok",
                "host": host,
                "scheme": settings.gateway_scheme,
                "port": settings.gateway_port,
                "domain_suffix": settings.domain_suffix,
                "preview_suffix": settings.preview_suffix,
                "upstream_host": settings.gateway_upstream_host,
                "routes": len(routes),
            }
            return 200, json.dumps(payload, ensure_ascii=False).encode(), "application/json"
        return 200, pages.status_page([r.to_public() for r in routes], host=host), "text/html; charset=utf-8"

    # -- http ------------------------------------------------------------
    async def _http_request(self, scope, receive, send) -> None:
        headers = _headers(scope)
        host = normalize_host(headers.get(b"host", b"").decode("latin-1"))
        path = _raw_path(scope)
        route = self.routes.resolve(host)

        if route is None:
            if self._is_console(host):
                status, body, content_type = self._console_response(path, host)
                await _send_body(send, status, body, content_type)
                return
            await _send_body(
                send, 404, pages.no_route(host), "text/html; charset=utf-8"
            )
            return

        query = scope.get("query_string", b"")
        url = route.upstream + path + (("?" + query.decode("latin-1")) if query else "")
        forwarded = _forward_headers(scope, headers, host, route)
        retry = path + (("?" + query.decode("latin-1")) if query else "")

        # buffer small bodies so the upstream gets a Content-Length: a chunked
        # request body is invisible to WSGI apps, and "my form posts are empty"
        # is not a bug worth shipping. Big uploads keep streaming.
        body, stream = await _read_body(receive, settings.gateway_body_buffer)

        try:
            request = self._http().build_request(
                scope["method"],
                url,
                headers=forwarded,
                content=body if stream is None else stream,
            )
            upstream = await self._http().send(request, stream=True)
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            logger.debug("gateway upstream down: %s (%s)", url, exc)
            await _send_body(
                send,
                502,
                pages.not_running(host, route.slug, route.environment, route.port, retry),
                "text/html; charset=utf-8",
            )
            return
        except httpx.HTTPError as exc:  # protocol errors, closed pool, ...
            await _send_body(
                send,
                502,
                pages.bad_upstream(host, route.slug, route.port, str(exc), retry),
                "text/html; charset=utf-8",
            )
            return

        try:
            response_headers = _response_headers(upstream, route, host)
            await send(
                {
                    "type": "http.response.start",
                    "status": upstream.status_code,
                    "headers": response_headers,
                }
            )
            async for chunk in upstream.aiter_raw():
                if chunk:
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        except (httpx.HTTPError, ConnectionClosed) as exc:
            # headers are already out; the only honest move is to cut the body
            logger.debug("gateway stream aborted: %s (%s)", url, exc)
            with contextlib.suppress(Exception):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await upstream.aclose()

    # -- websocket (vite HMR and friends) --------------------------------
    async def _websocket(self, scope, receive, send) -> None:
        headers = _headers(scope)
        host = normalize_host(headers.get(b"host", b"").decode("latin-1"))
        route = self.routes.resolve(host)
        message = await receive()
        if message["type"] != "websocket.connect":
            return
        if route is None:
            await send({"type": "websocket.close", "code": 1008})
            return

        path = _raw_path(scope)
        query = scope.get("query_string", b"")
        url = (
            f"ws://{settings.gateway_upstream_host}:{route.port}{path}"
            + (("?" + query.decode("latin-1")) if query else "")
        )
        # The handshake headers belong to *this* hop: `Sec-WebSocket-Key`,
        # `Upgrade` and friends are regenerated by the websockets client, and
        # forwarding them produces a duplicate-key 400. Only the headers that
        # carry identity survive.
        forwarded = {
            key.decode("latin-1"): value.decode("latin-1")
            for key, value in headers.items()
            if key in {b"cookie", b"authorization", b"origin", b"user-agent", b"accept-language"}
        }
        forwarded.update(
            {
                "Host": f"localhost:{route.port}",
                "X-Forwarded-Host": host,
                "X-Forwarded-Proto": settings.gateway_scheme,
                "X-Forwarded-For": _client_ip(scope, headers),
                "X-Workbench-Route": f"{route.slug}:{route.environment}",
            }
        )

        try:
            upstream = await ws_connect(
                url,
                additional_headers=forwarded,
                subprotocols=scope.get("subprotocols") or None,
                proxy=None,
                compression=None,
                open_timeout=8,
                # the app owns its heartbeats; a proxy-level ping would double them
                ping_interval=None,
                max_size=None,
            )
        except Exception as exc:  # noqa: BLE001 - handshake failure is an expected state
            logger.debug("gateway ws refused: %s (%s)", url, exc)
            await send({"type": "websocket.close", "code": 1013})
            return

        await send({"type": "websocket.accept", "subprotocol": upstream.subprotocol})

        async def client_to_upstream() -> None:
            while True:
                item = await receive()
                if item["type"] == "websocket.receive":
                    if item.get("text") is not None:
                        await upstream.send(item["text"])
                    else:
                        await upstream.send(item.get("bytes") or b"")
                else:
                    await upstream.close(code=item.get("code", 1000))
                    return

        async def upstream_to_client() -> None:
            try:
                async for payload in upstream:
                    if isinstance(payload, str):
                        await send({"type": "websocket.send", "text": payload})
                    else:
                        await send({"type": "websocket.send", "bytes": payload})
            except ConnectionClosed:
                pass

        tasks = [asyncio.create_task(client_to_upstream()), asyncio.create_task(upstream_to_client())]
        try:
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(BaseException):
                    await task
        finally:
            with contextlib.suppress(Exception):
                await upstream.close()
            with contextlib.suppress(Exception):
                await send({"type": "websocket.close", "code": 1000})


# ---------------------------------------------------------------------------
# header plumbing
# ---------------------------------------------------------------------------


def _headers(scope: dict) -> dict[bytes, bytes]:
    return {key.lower(): value for key, value in scope.get("headers", [])}


def _raw_path(scope: dict) -> str:
    raw = scope.get("raw_path") or b""
    if isinstance(raw, str):  # pragma: no cover - some servers hand back text
        raw = raw.encode("latin-1")
    raw = raw.split(b"?", 1)[0]
    if not raw:
        return scope.get("path") or "/"
    return raw.decode("latin-1") or "/"


def _client_ip(scope: dict, headers: dict[bytes, bytes]) -> str:
    forwarded = headers.get(b"x-forwarded-for", b"").decode("latin-1").split(",")[0].strip()
    if forwarded:
        return forwarded
    client = scope.get("client") or ("", 0)
    return str(client[0])


def _forward_headers(
    scope: dict, headers: dict[bytes, bytes], host: str, route: Route
) -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    for key, value in headers.items():
        if key in HOP_BY_HOP or key in REWRITTEN:
            continue
        out[key] = value
    # the upstream sees a loopback authority so host-validating dev servers accept
    # it; the real public host is one header away
    out[b"host"] = f"localhost:{route.port}".encode()
    out[b"x-forwarded-host"] = host.encode("latin-1")
    out[b"x-forwarded-proto"] = settings.gateway_scheme.encode()
    out[b"x-forwarded-port"] = str(settings.gateway_port).encode()
    out[b"x-real-ip"] = _client_ip(scope, headers).encode("latin-1")
    prior = headers.get(b"x-forwarded-for", b"").decode("latin-1").strip()
    client = _client_ip(scope, {})
    out[b"x-forwarded-for"] = f"{prior}, {client}".encode("latin-1") if prior else client.encode("latin-1")
    out[b"x-workbench-route"] = f"{route.slug}:{route.environment}".encode()
    return out


def _response_headers(response: httpx.Response, route: Route, host: str) -> list[tuple[bytes, bytes]]:
    headers: list[tuple[bytes, bytes]] = []
    for key, value in response.headers.raw:
        name = key.lower()
        if name in HOP_BY_HOP:
            continue
        if name == b"location" and value:
            value = _rewrite_location(value, route, host)
        headers.append((name, value))
    headers.append((b"x-workbench-route", f"{route.slug}:{route.environment}".encode()))
    return headers


def _rewrite_location(value: bytes, route: Route, host: str) -> bytes:
    """Keep redirects on the public host instead of leaking the loopback one.

    A dev server that answers ``Location: http://localhost:4204/login`` would send
    the browser off the domain — and into a port that the outside world cannot
    reach. Only the upstream's own origin is rewritten; anything else (an SSO
    provider, an external URL) is left alone.
    """
    text = value.decode("latin-1")
    for prefix in (route.upstream, f"http://localhost:{route.port}", f"http://127.0.0.1:{route.port}"):
        if text.startswith(prefix):
            public = conventions.origin_for(host, route.port, via_gateway=True)
            return (public + text[len(prefix):]).encode("latin-1")
    return value


# httpx needs an *async* iterator for a streamed request body
async def _body_stream(receive) -> AsyncIterator[bytes]:
    while True:
        message = await receive()
        if message["type"] == "http.request":
            body = message.get("body") or b""
            if body:
                yield body
            if not message.get("more_body"):
                return
        elif message["type"] == "http.disconnect":
            return
        else:  # pragma: no cover - defensive
            return


async def _read_body(receive, limit: int) -> tuple[bytes | None, AsyncIterator[bytes] | None]:
    """Either the whole body, or a stream that continues where the buffer stopped.

    Exactly one of the two is returned. Buffering is what lets the upstream see a
    ``Content-Length``; past the limit the proxy stops collecting and hands the
    request over as a stream, so a 400 MB upload does not become 400 MB of RAM.
    """
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != "http.request":
            return b"".join(chunks), None
        chunk = message.get("body") or b""
        if chunk:
            chunks.append(chunk)
            size += len(chunk)
        if not message.get("more_body"):
            return b"".join(chunks), None
        if size >= limit:
            return None, _continue_body(b"".join(chunks), receive)


async def _continue_body(head: bytes, receive) -> AsyncIterator[bytes]:
    yield head
    async for chunk in _body_stream(receive):
        yield chunk


async def _send_body(send, status: int, body: bytes, content_type: str) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", content_type.encode()),
                (b"content-length", str(len(body)).encode()),
                (b"cache-control", b"no-store"),
                (b"x-workbench-gateway", b"1"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def create_app() -> Gateway:
    return Gateway()


app = create_app()
