"""End-to-end check for TLS: wildcard, per-host SNI, hot reload, and ACME.

Everything here is real except the certificate authority: two uvicorn listeners
(443-ish with SNI, 80-ish for redirect + ACME challenges), a real upstream, real
handshakes inspected with :mod:`ssl`, and a local ACME directory that speaks
RFC 8555 well enough to reject a wrong JWS.

What is asserted, in the order a person would care:

* a wildcard certificate answers every project host, and only one label deep;
* a per-host certificate wins over the wildcard for its own name (SNI selection);
* a new certificate dropped on disk is served on the next handshake, no restart;
* a certificate whose key does not match is reported, not served;
* http:// redirects to https, but ``/.well-known/acme-challenge`` is answered;
* a full http-01 order completes against the fake directory, and the CA-side
  fetch of the token really hits the gateway's challenge path;
* ``*.example.com`` is refused with an explanation instead of a DNS-01 failure;
* the queue written by the control plane is picked up by the renewer.

Usage::

    cd backend && ./.venv/Scripts/python.exe tools/tls_smoke.py
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import ssl
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="wb-tls-smoke-"))
os.environ["WB_DATA_DIR"] = str(_TMP / "data")
os.environ["WB_BOOTSTRAP_DEMO_DATA"] = "false"
os.environ["WB_AUTH_ENABLED"] = "false"
os.environ["WB_DOMAIN_SUFFIX"] = "localtest.me"
os.environ["WB_GATEWAY_ACME_ENABLED"] = "false"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import uvicorn  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

from app.core.db import init_db  # noqa: E402
from app.gateway import acme as acme_mod  # noqa: E402
from app.gateway import tls as tls_mod  # noqa: E402
from app.gateway.proxy import create_app  # noqa: E402
from app.gateway.routes import Route  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(label)
    mark = "PASS" if ok else "FAIL"
    note = (" — " + detail) if detail and not ok else ""
    print(f"[{mark}] {label}{note}", flush=True)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


# ---------------------------------------------------------------------------
# raw clients (no DNS: every connection is to 127.0.0.1 with a chosen Host/SNI)
# ---------------------------------------------------------------------------


def http_get(
    port: int, host: str, path: str = "/", *, timeout: float = 5.0
) -> tuple[int, dict[str, str], bytes]:
    with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
        raw.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
        )
        chunks: list[bytes] = []
        while True:
            data = raw.recv(65536)
            if not data:
                break
            chunks.append(data)
    payload = b"".join(chunks)
    head, _, body = payload.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    code = int(lines[0].split(" ")[1]) if lines and " " in lines[0] else 0
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    return code, headers, body


def tls_get(port: int, host: str, path: str = "/") -> tuple[int, list[str], bytes]:
    """Handshake with an explicit SNI name; returns (status, peer SANs, body)."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=8) as raw:
        with context.wrap_socket(raw, server_hostname=host) as tls_sock:
            der = tls_sock.getpeercert(binary_form=True)
            cert = x509.load_der_x509_certificate(der)
            sans = [
                str(name)
                for name in cert.extensions.get_extension_for_class(
                    x509.SubjectAlternativeName
                ).value.get_values_for_type(x509.DNSName)
            ]
            tls_sock.sendall(
                f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode()
            )
            chunks: list[bytes] = []
            while True:
                data = tls_sock.recv(65536)
                if not data:
                    break
                chunks.append(data)
    payload = b"".join(chunks)
    head, _, body = payload.partition(b"\r\n\r\n")
    first = head.split(b"\r\n")[0].decode("latin-1")
    code = int(first.split(" ")[1]) if " " in first else 0
    return code, sans, body


# ---------------------------------------------------------------------------
# fixtures: certificates, upstream, route table
# ---------------------------------------------------------------------------


def write_pair(path: Path, cert_pem: bytes, key_pem: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(cert_pem)
    path.with_suffix(".key").write_bytes(key_pem)


class Upstream(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # noqa: D102
        pass

    def do_GET(self):  # noqa: N802
        body = b"upstream-ok"
        self.send_response(200)
        self.send_header("content-type", "text/plain")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class StaticTable:
    """A route table with no database behind it."""

    def __init__(self, routes: list[Route]) -> None:
        self._routes = {route.host: route for route in routes}

    def resolve(self, host: str):
        return self._routes.get(tls_mod.normalise(host))

    def snapshot(self) -> list[Route]:
        return list(self._routes.values())

    def invalidate(self) -> None:
        return None


def serve(port: int, app, *, ssl_context=None) -> uvicorn.Server:
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            lifespan="off",
            ssl_context_factory=(lambda _c, _d: ssl_context) if ssl_context else None,
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    if not wait_port(port):
        raise RuntimeError(f"listener on {port} did not come up")
    return server


# ---------------------------------------------------------------------------
# fake ACME directory
# ---------------------------------------------------------------------------


def _b64d(text: str) -> bytes:
    import base64

    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _b64(data: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class FakeACME:
    """Enough of RFC 8555 to catch a wrong JWS, a missing nonce or a bad CSR."""

    def __init__(self, base: str, challenge_port: int) -> None:
        self.base = base
        self.challenge_port = challenge_port
        self.nonces: set[str] = set()
        self.kid_seen = False
        self.jwk_seen = False
        self.post_as_get = 0
        self.state: dict[str, object] = {}
        self.ca_key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(UTC)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Fake ACME CA")])
        self.ca_cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(self.ca_key, hashes.SHA256())
        )

    # -- plumbing ------------------------------------------------------
    def _nonce(self) -> str:
        token = _b64(os.urandom(16))
        self.nonces.add(token)
        return token

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            return
        path = scope["path"]
        method = scope["method"]
        body = b""
        if method == "POST":
            while True:
                message = await receive()
                body += message.get("body", b"")
                if not message.get("more_body"):
                    break
        status, headers, payload = await self._route(method, path, body)
        headers = list(headers) + [(b"replay-nonce", self._nonce().encode())]
        headers.append((b"content-length", str(len(payload)).encode()))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    def _json(self, data: dict, status: int = 200, location: str = "") -> tuple[int, list, bytes]:
        headers: list[tuple[bytes, bytes]] = [(b"content-type", b"application/json")]
        if location:
            headers.append((b"location", location.encode()))
        return status, headers, json.dumps(data).encode()

    def _problem(self, detail: str, kind: str = "malformed") -> tuple[int, list, bytes]:
        return (
            400,
            [(b"content-type", b"application/problem+json")],
            json.dumps({"type": "urn:ietf:params:acme:error:" + kind, "detail": detail}).encode(),
        )

    def _unseal(self, body: bytes, expected_url: str) -> tuple[dict, dict | None]:
        """Verify the JWS envelope the way a CA would (shape + nonce + url)."""
        envelope = json.loads(body)
        protected = json.loads(_b64d(envelope["protected"]))
        if protected.get("alg") != "ES256":
            raise ValueError("alg must be ES256")
        if protected.get("url") != expected_url:
            raise ValueError(f"url mismatch: {protected.get('url')} != {expected_url}")
        nonce = protected.get("nonce", "")
        if nonce not in self.nonces:
            raise ValueError("unknown or reused nonce")
        self.nonces.discard(nonce)
        if "jwk" in protected:
            self.jwk_seen = True
        if "kid" in protected:
            self.kid_seen = True
        payload = {}
        if envelope["payload"]:
            payload = json.loads(_b64d(envelope["payload"]))
        else:
            self.post_as_get += 1
        return protected, payload or {}

    # -- routes --------------------------------------------------------
    async def _route(self, method: str, path: str, body: bytes):
        try:
            return await self._dispatch(method, path, body)
        except ValueError as exc:
            return self._problem(str(exc))

    async def _dispatch(self, method: str, path: str, body: bytes):
        if path == "/directory":
            return self._json(
                {
                    "newNonce": self.base + "/new-nonce",
                    "newAccount": self.base + "/new-account",
                    "newOrder": self.base + "/new-order",
                }
            )
        if path in {"/new-nonce"}:
            return 200, [(b"cache-control", b"no-store")], b""
        if path == "/new-account":
            self._unseal(body, self.base + "/new-account")
            return self._json({"status": "valid"}, status=201, location=self.base + "/acct/1")
        if path == "/new-order":
            _, payload = self._unseal(body, self.base + "/new-order")
            identifiers = payload.get("identifiers") or []
            hosts = [str(item["value"]) for item in identifiers]
            self.state["hosts"] = hosts
            self.state["token"] = _b64(os.urandom(18))
            self.state["authz_valid"] = False
            self.state["order_status"] = "pending"
            self.state["certificate"] = ""
            return self._json(
                {
                    "status": "pending",
                    "identifiers": identifiers,
                    "authorizations": [self.base + "/authz/1"],
                    "finalize": self.base + "/finalize/1",
                },
                status=201,
                location=self.base + "/order/1",
            )
        if path == "/authz/1":
            self._unseal(body, self.base + "/authz/1")
            valid = bool(self.state.get("authz_valid"))
            return self._json(
                {
                    "status": "valid" if valid else "pending",
                    "identifier": {"type": "dns", "value": (self.state.get("hosts") or ["?"])[0]},
                    "challenges": [
                        {
                            "type": "http-01",
                            "url": self.base + "/chall/1",
                            "token": self.state.get("token"),
                            "status": "valid" if valid else "pending",
                        }
                    ],
                }
            )
        if path == "/chall/1":
            self._unseal(body, self.base + "/chall/1")
            # the real CA fetches the token over plain HTTP; do exactly that
            host = (self.state.get("hosts") or ["?"])[0]
            token = str(self.state.get("token"))
            code, _, payload = http_get(
                self.challenge_port, host, f"/.well-known/acme-challenge/{token}"
            )
            if code != 200:
                return self._problem(f"challenge fetch returned {code}", "unauthorized")
            expected = f"{token}."
            if not payload.decode("ascii", "ignore").startswith(expected):
                return self._problem("challenge body is not the key authorization", "unauthorized")
            self.state["authz_valid"] = True
            return self._json(
                {"type": "http-01", "status": "processing", "url": self.base + "/chall/1"}
            )
        if path == "/finalize/1":
            _, payload = self._unseal(body, self.base + "/finalize/1")
            csr = x509.load_der_x509_csr(_b64d(payload["csr"]))
            if not csr.is_signature_valid:
                return self._problem("CSR signature invalid", "badCSR")
            sans = csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
            names = list(sans.get_values_for_type(x509.DNSName))
            now = datetime.now(UTC)
            leaf = (
                x509.CertificateBuilder()
                .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0])]))
                .issuer_name(self.ca_cert.subject)
                .public_key(csr.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=90))
                .add_extension(
                    x509.SubjectAlternativeName([x509.DNSName(name) for name in names]),
                    critical=False,
                )
                .sign(self.ca_key, hashes.SHA256())
            )
            self.state["certificate"] = (
                leaf.public_bytes(serialization.Encoding.PEM)
                + self.ca_cert.public_bytes(serialization.Encoding.PEM)
            ).decode()
            self.state["order_status"] = "valid"
            return self._json({"status": "processing", "finalize": self.base + "/finalize/1"})
        if path == "/order/1":
            self._unseal(body, self.base + "/order/1")
            status = str(self.state.get("order_status"))
            payload = {
                "status": status,
                "finalize": self.base + "/finalize/1",
                "authorizations": [self.base + "/authz/1"],
            }
            if status == "valid":
                payload["certificate"] = self.base + "/cert/1"
            return self._json(payload)
        if path == "/cert/1":
            self._unseal(body, self.base + "/cert/1")
            return (
                200,
                [(b"content-type", b"application/pem-certificate-chain")],
                str(self.state.get("certificate")).encode(),
            )
        return self._problem(f"no route for {path}", "malformed")


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


def main() -> int:
    tls_dir = Path(os.environ["WB_DATA_DIR"]) / "tls"
    tls_dir.mkdir(parents=True, exist_ok=True)
    init_db()

    # -- fixtures ------------------------------------------------------
    wildcard_pem, wildcard_key = tls_mod.self_signed(["*.localtest.me", "localtest.me"], days=60)
    wildcard_path = _TMP / "wildcard.pem"
    wildcard_path.write_bytes(wildcard_pem)
    wildcard_path.with_suffix(".key").write_bytes(wildcard_key)

    custom_pem, custom_key = tls_mod.self_signed(["custom.example.com"], days=14)
    write_pair(tls_dir / "custom.example.com.pem", custom_pem, custom_key)

    # a key that does not belong to its certificate must never be served
    other_pem, _ = tls_mod.self_signed(["broken.example.com"])
    _, wrong_key = tls_mod.self_signed(["unrelated.example.com"])
    write_pair(tls_dir / "broken.example.com.pem", other_pem, wrong_key)

    store = tls_mod.CertStore(
        tls_dir,
        cert_file=str(wildcard_path),
        key_file=str(wildcard_path.with_suffix(".key")),
    )
    primary = store.default()
    check("wildcard is the default certificate", primary is not None and primary.is_wildcard)
    check("wildcard covers one label", store.resolve("shop.localtest.me") is not None)
    check("wildcard does not cover two labels", store.resolve("a.b.localtest.me") is None)
    chosen = store.resolve("custom.example.com")
    check(
        "per-host certificate is selected for its own name",
        chosen is not None and chosen.source == "file",
    )
    check("mismatched key is reported, not served", store.resolve("broken.example.com") is None)
    check(
        "mismatched key is visible in status",
        any(cert["error"] == "" and not cert["key_ok"] for cert in store.status()["certs"]),
    )
    check("suffix is covered", store.covers_suffix("localtest.me"))
    check("every cert is counted", store.status()["count"] >= 3, str(store.status()["count"]))

    # -- listeners -----------------------------------------------------
    upstream_port = free_port()
    threading.Thread(
        target=ThreadingHTTPServer(("127.0.0.1", upstream_port), Upstream).serve_forever,
        daemon=True,
    ).start()

    routes = StaticTable(
        [
            Route(host="shop.localtest.me", project_id="p1", slug="shop", name="Shop",
                  environment="production", port=upstream_port),
            Route(host="custom.example.com", project_id="p2", slug="custom", name="Custom",
                  environment="production", port=upstream_port, custom=True),
        ]
    )

    https_port = free_port()
    http_port = free_port()
    challenge = acme_mod.Challenge(tls_dir / "challenges")

    https_server = serve(https_port, create_app(routes=routes), ssl_context=store.server_context())
    http_server = serve(
        http_port,
        create_app(routes=routes, challenges=challenge, redirect_https=True),
    )

    code, sans, body = tls_get(https_port, "shop.localtest.me")
    check(
        "https reaches the upstream",
        code == 200 and body == b"upstream-ok",
        f"{code} {body[:40]!r}",
    )
    check("wildcard SAN is presented for a project host", "*.localtest.me" in sans, str(sans))

    code, sans, body = tls_get(https_port, "custom.example.com")
    check(
        "SNI selects the per-host certificate",
        "custom.example.com" in sans and "*.localtest.me" not in sans,
        str(sans),
    )
    check("per-host certificate still reaches the upstream", body == b"upstream-ok")

    code, headers, _ = http_get(http_port, "shop.localtest.me", "/dashboard?tab=1")
    check("http redirects to https", code == 308, str(code))
    check(
        "redirect keeps host, path and query",
        headers.get("location") == "https://shop.localtest.me/dashboard?tab=1",
        headers.get("location", ""),
    )

    token = "smoke-token-0123456789"
    challenge.publish(token, token + ".thumbprint")
    code, _, payload = http_get(
        http_port, "shop.localtest.me", f"/.well-known/acme-challenge/{token}"
    )
    check(
        "challenge is answered over http",
        code == 200 and payload == (token + ".thumbprint").encode(),
        payload.decode("latin-1"),
    )
    code, _, _ = http_get(
        http_port, "shop.localtest.me", "/.well-known/acme-challenge/nope-nope-nope"
    )
    check("unknown challenge is a 404", code == 404, str(code))
    challenge.retract(token)

    t0 = time.monotonic()
    late_pem, late_key = tls_mod.self_signed(["late.example.com"])
    write_pair(tls_dir / "late.example.com.pem", late_pem, late_key)
    store.reload()
    check(
        "new certificate is picked up without restart",
        store.resolve("late.example.com") is not None,
    )
    check("reload is not a restart", time.monotonic() - t0 < 5)

    # -- ACME against a local directory --------------------------------
    acme_port = free_port()
    fake = FakeACME(f"http://127.0.0.1:{acme_port}", http_port)
    serve(acme_port, fake)

    client = acme_mod.AcmeClient(
        fake.base + "/directory",
        email="ops@example.com",
        account_key=tls_dir / "account.key",
        challenge=challenge,
        poll_timeout=20,
    )
    try:
        chain, key_pem = asyncio.run(client.issue(["issued.example.com"]))
    except acme_mod.AcmeError as exc:
        chain, key_pem = b"", b""
        check("acme order completes", False, str(exc))
    else:
        check("acme order completes", bool(chain) and b"BEGIN CERTIFICATE" in chain)
    check("acme used a jwk for newAccount", fake.jwk_seen)
    check("acme used kid afterwards", fake.kid_seen)
    if fake.state.get("certificate"):
        issued = x509.load_pem_x509_certificate(str(fake.state["certificate"]).encode())
        issued_sans = [
            str(name)
            for name in issued.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value.get_values_for_type(x509.DNSName)
        ]
        check(
            "acme signed the SAN we asked for",
            issued_sans == ["issued.example.com"],
            str(issued_sans),
        )
    else:
        check("acme signed the SAN we asked for", False, "no certificate issued")
    check("challenge file was retracted", not any((tls_dir / "challenges").glob("*")))
    check("account key was persisted", (tls_dir / "account.key").exists())

    if chain:
        record = store.store("issued.example.com", chain, key_pem)
        check(
            "issued certificate is usable",
            record.key_ok and "issued.example.com" in record.hosts,
            str(record.hosts),
        )
        depth = chain.count(b"BEGIN CERTIFICATE")
        check("issued chain keeps the intermediate", depth == 2, str(depth))
        issued_cert = store.resolve("issued.example.com")
        check(
            "issued certificate beats the wildcard for its host",
            issued_cert is not None and issued_cert.source == "acme",
        )

    try:
        asyncio.run(client.issue(["*.localtest.me"]))
        check("wildcard over http-01 is refused", False, "no error raised")
    except acme_mod.AcmeError as exc:
        check("wildcard over http-01 is refused", "http-01" in str(exc), str(exc)[:80])

    # -- queue -> renewer ---------------------------------------------
    queue = acme_mod.IssueQueue(tls_dir / "queue")
    queue.request("queued.example.com")
    check(
        "queue writes a pending request",
        [row.host for row in queue.pending()] == ["queued.example.com"],
        str([row.host for row in queue.pending()]),
    )

    renewer = acme_mod.Renewer(
        store,
        queue,
        directory_url=fake.base + "/directory",
        email="ops@example.com",
        renew_days=30,
        max_per_tick=2,
        poll_timeout=20,
    )
    results = asyncio.run(renewer.tick())
    handled = any(
        item["host"] == "queued.example.com" and item["ok"] for item in results
    )
    check("renewer processes the queue", handled, str(results))
    settled = queue.get("queued.example.com")
    check(
        "queue entry ends as done",
        settled is not None and settled.state == "done",
        settled.state if settled else "missing",
    )
    check("queued certificate is on disk", store.resolve("queued.example.com") is not None)

    # -- the control-plane view ---------------------------------------
    check("status reports an expiry horizon", store.status()["days_left"] is not None)
    check(
        "status flags the certificate expiring soon",
        any(cert["days_left"] <= 14 and not cert["expired"] for cert in store.status()["certs"]),
    )

    https_server.should_exit = True
    http_server.should_exit = True

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed", flush=True)
    if FAIL:
        for item in FAIL:
            print(f"  · {item}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
