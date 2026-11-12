"""ACME (RFC 8555) over http-01 — free certificates without a client library.

Why an in-tree client instead of ``certbot``: the gateway already owns port 80,
and the whole order is six HTTP calls. Shelling out to an external ACME client
would mean another process, another config format, another way for a certificate
to be "half renewed" — and it would still need a hook to tell us to reload. Ninety
lines of JWS is the smaller liability.

What it does not do: **wildcards**. ``*.example.com`` can only be validated over
DNS-01, which needs credentials for the DNS provider. If you want a wildcard,
bring one (:mod:`app.gateway.tls` loads it and it takes precedence); this module
issues one certificate per host name instead, which is exactly what http-01 is
for.

The order, in the spec's own vocabulary: ``newAccount`` once (the account key is
stored next to the certificates), then per certificate ``newOrder`` → for each
authorization ``http-01`` → serve the token from
``<tls_dir>/challenges/<token>`` → ``finalize`` with a locally generated CSR →
download the chain. The private key never leaves the machine; only the CSR does.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.gateway import tls as tls_mod

logger = logging.getLogger("workbench.gateway.acme")

USER_AGENT = "ai-workbench-gateway/0.1 (+acme; python-httpx)"
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")


class AcmeError(RuntimeError):
    """A problem document, or a protocol state we could not get out of."""

    def __init__(self, message: str, *, kind: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64d(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


@dataclass(slots=True)
class Challenge:
    """Where the http-01 token goes and how it is answered."""

    directory: Path

    def publish(self, token: str, key_authorization: str) -> None:
        if not TOKEN_RE.match(token):  # never let a server pick a path
            raise AcmeError(f"ACME 返回了非法 token: {token!r}")
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / token
        target.write_text(key_authorization, encoding="ascii")

    def retract(self, token: str) -> None:
        if not TOKEN_RE.match(token or ""):
            return
        try:
            (self.directory / token).unlink()
        except OSError:
            pass

    def read(self, token: str) -> str | None:
        if not TOKEN_RE.match(token or ""):
            return None
        try:
            return (self.directory / token).read_text(encoding="ascii")
        except OSError:
            return None


class AcmeClient:
    """A minimal, synchronous-looking async ACME client bound to one directory."""

    def __init__(
        self,
        directory_url: str,
        *,
        email: str = "",
        account_key: Path,
        challenge: Challenge,
        timeout: float = 20.0,
        poll_timeout: float = 180.0,
    ) -> None:
        self.directory_url = directory_url.rstrip("/")
        self.email = email.strip()
        self.account_key_path = account_key
        self.challenge = challenge
        self.timeout = timeout
        self.poll_timeout = poll_timeout
        self._key: Any = None
        self._kid: str = ""
        self._nonce: str = ""
        self._endpoints: dict[str, str] = {}
        self._thumbprint: str = ""

    # -- account key -----------------------------------------------------
    def _load_key(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        if self._key is not None:
            return self._key
        if self.account_key_path.exists():
            self._key = serialization.load_pem_private_key(
                self.account_key_path.read_bytes(), password=None
            )
        else:
            self._key = ec.generate_private_key(ec.SECP256R1())
            self.account_key_path.parent.mkdir(parents=True, exist_ok=True)
            pem = self._key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            tmp = self.account_key_path.with_suffix(".tmp")
            tmp.write_bytes(pem)
            try:
                os.chmod(tmp, 0o600)
            except OSError:  # pragma: no cover - Windows
                pass
            os.replace(tmp, self.account_key_path)
            logger.info("generated ACME account key at %s", self.account_key_path)
        numbers = self._key.public_key().public_numbers()
        self._thumbprint = _thumbprint(
            {
                "crv": "P-256",
                "kty": "EC",
                "x": _b64(numbers.x.to_bytes(32, "big")),
                "y": _b64(numbers.y.to_bytes(32, "big")),
            }
        )
        return self._key

    def _jwk(self) -> dict[str, str]:
        numbers = self._load_key().public_key().public_numbers()
        return {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64(numbers.x.to_bytes(32, "big")),
            "y": _b64(numbers.y.to_bytes(32, "big")),
        }

    def _sign(self, data: bytes) -> bytes:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

        der = self._load_key().sign(data, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big") + s.to_bytes(32, "big")

    # -- transport -------------------------------------------------------
    def _remember_nonce(self, response: httpx.Response) -> None:
        nonce = response.headers.get("replay-nonce")
        if nonce:
            self._nonce = nonce

    async def _signed(
        self,
        client: httpx.AsyncClient,
        url: str,
        payload: dict[str, Any] | None,
        *,
        use_jwk: bool = False,
        accept: str = "application/json",
    ) -> httpx.Response:
        if not self._nonce:
            await self._fetch_nonce(client)
        protected: dict[str, Any] = {"alg": "ES256", "nonce": self._nonce, "url": url}
        if use_jwk or not self._kid:
            protected["jwk"] = self._jwk()
        else:
            protected["kid"] = self._kid
        encoded_protected = _b64(json.dumps(protected, separators=(",", ":")).encode())
        encoded_payload = "" if payload is None else _b64(json.dumps(payload).encode())
        signature = _b64(self._sign(f"{encoded_protected}.{encoded_payload}".encode()))
        body = {
            "protected": encoded_protected,
            "payload": encoded_payload,
            "signature": signature,
        }
        response = await client.post(
            url,
            content=json.dumps(body),
            headers={
                "Content-Type": "application/jose+json",
                "Accept": accept,
                "User-Agent": USER_AGENT,
            },
        )
        self._remember_nonce(response)
        if response.status_code >= 400:
            raise _problem(response)
        return response

    async def _fetch_nonce(self, client: httpx.AsyncClient) -> None:
        response = await client.get(
            self._endpoints.get("newNonce") or self.directory_url + "/new-nonce",
            headers={"User-Agent": USER_AGENT},
        )
        self._remember_nonce(response)
        if not self._nonce:
            raise AcmeError("ACME 目录没有返回 Replay-Nonce")

    async def _directory(self, client: httpx.AsyncClient) -> dict[str, str]:
        if self._endpoints:
            return self._endpoints
        response = await client.get(self.directory_url, headers={"User-Agent": USER_AGENT})
        if response.status_code != 200:
            raise AcmeError(
                f"无法读取 ACME 目录 {self.directory_url}（HTTP {response.status_code}）",
                status=response.status_code,
            )
        data = response.json()
        for key in ("newNonce", "newAccount", "newOrder"):
            if key not in data:
                raise AcmeError(f"ACME 目录缺少 {key}")
        self._endpoints = {
            "newNonce": data["newNonce"],
            "newAccount": data["newAccount"],
            "newOrder": data["newOrder"],
            "revokeCert": data.get("revokeCert", ""),
            "keyChange": data.get("keyChange", ""),
        }
        self._remember_nonce(response)
        return self._endpoints

    async def register(self, client: httpx.AsyncClient | None = None) -> str:
        """``newAccount`` (idempotent: a returning key gets its old kid back)."""
        own = client is None
        client = client or httpx.AsyncClient(timeout=self.timeout, trust_env=False)
        try:
            endpoints = await self._directory(client)
            payload: dict[str, Any] = {"termsOfServiceAgreed": True}
            if self.email:
                payload["contact"] = [f"mailto:{self.email}"]
            response = await self._signed(
                client, endpoints["newAccount"], payload, use_jwk=True
            )
            self._kid = response.headers.get("location", "")
            if not self._kid:
                body = response.json() if response.content else {}
                self._kid = str(body.get("id") or "")
            if not self._kid:
                raise AcmeError("ACME 账号创建成功但没有返回 kid")
            return self._kid
        finally:
            if own:
                await client.aclose()

    async def issue(self, hosts: list[str]) -> tuple[bytes, bytes]:
        """Issue one certificate for every host name. Returns ``(chain, key)``."""
        names = [tls_mod.normalise(host) for host in hosts if tls_mod.normalise(host)]
        if not names:
            raise AcmeError("没有要签发的域名")
        wildcards = [name for name in names if name.startswith("*.")]
        if wildcards:
            raise AcmeError(
                "通配符证书不能走 http-01（" + ", ".join(wildcards) + "）："
                "请配置一张 *.域名 的证书文件（WB_GATEWAY_TLS_CERT/KEY，优先），"
                "自动签发只对逐个域名生效。"
            )

        pair = tls_mod.build_csr(names)
        async with httpx.AsyncClient(timeout=self.timeout, trust_env=False) as client:
            await self._directory(client)
            if not self._kid:
                await self.register(client)

            order = await self._new_order(client, names)
            for authz_url in order.get("authorizations", []):
                await self._authorize(client, authz_url)

            finalize = order.get("finalize") or ""
            if not finalize:
                raise AcmeError("订单没有 finalize 地址")
            response = await self._signed(client, finalize, {"csr": _b64(pair.csr_der)})
            if response.content:
                order.update(response.json())
            order = await self._poll_order(client, order)

            certificate_url = order.get("certificate") or ""
            if not certificate_url:
                raise AcmeError("证书签发完成但订单没有证书地址")
            chain = await self._signed(
                client, certificate_url, None, accept="application/pem-certificate-chain"
            )
            return chain.content, pair.key_pem

    # -- order steps -----------------------------------------------------
    async def _new_order(self, client: httpx.AsyncClient, names: list[str]) -> dict[str, Any]:
        payload = {"identifiers": [{"type": "dns", "value": name} for name in names]}
        response = await self._signed(client, self._endpoints["newOrder"], payload)
        order = response.json()
        # the order object has no self link; polling needs the Location header
        order["url"] = response.headers.get("location", "")
        if order.get("status") == "invalid":
            raise AcmeError("订单被 ACME 服务端直接判为无效：" + json.dumps(order)[:300])
        return order

    async def _authorize(self, client: httpx.AsyncClient, url: str) -> None:
        response = await self._signed(client, url, None)
        authz = response.json()
        status = str(authz.get("status") or "")
        if status == "valid":
            return
        if status in {"invalid", "deactivated", "expired", "revoked"}:
            raise AcmeError(f"授权已处于 {status} 状态：{authz.get('identifier')}")

        challenges = authz.get("challenges") or []
        http01 = next((item for item in challenges if item.get("type") == "http-01"), None)
        if http01 is None:
            kinds = ", ".join(str(item.get("type")) for item in challenges) or "无"
            raise AcmeError(f"ACME 只提供了 {kinds} 挑战，没有 http-01")

        token = str(http01.get("token") or "")
        key_authorization = f"{token}.{self._thumbprint}"
        self.challenge.publish(token, key_authorization)
        try:
            await self._signed(client, str(http01.get("url") or ""), {})
            await self._poll_authz(client, url)
        finally:
            self.challenge.retract(token)

    async def _poll_authz(self, client: httpx.AsyncClient, url: str) -> None:
        deadline = time.monotonic() + self.poll_timeout
        delay = 1.0
        while time.monotonic() < deadline:
            response = await self._signed(client, url, None)
            authz = response.json()
            status = str(authz.get("status") or "")
            if status == "valid":
                return
            if status in {"invalid", "deactivated", "expired", "revoked"}:
                raise AcmeError(_challenge_error(authz) or f"授权失败：{status}")
            await asyncio.sleep(delay)
            delay = min(delay * 1.6, 8.0)
        raise AcmeError("等待 ACME 验证超时（检查 80 端口是否可以从公网访问）")

    async def _poll_order(self, client: httpx.AsyncClient, order: dict[str, Any]) -> dict[str, Any]:
        deadline = time.monotonic() + self.poll_timeout
        delay = 1.0
        while time.monotonic() < deadline:
            status = str(order.get("status") or "")
            if status == "valid":
                return order
            if status == "invalid":
                raise AcmeError("订单状态为 invalid：" + json.dumps(order)[:300])
            url = str(order.get("url") or "")
            if not url:
                # a directory that returns the whole order from finalize needs no poll
                return order
            await asyncio.sleep(delay)
            delay = min(delay * 1.6, 8.0)
            response = await self._signed(client, url, None)
            if response.content:
                polled = response.json()
                polled["url"] = url
                order = polled
        raise AcmeError("等待证书签发超时")


def _thumbprint(jwk: dict[str, str]) -> str:
    parts = {key: jwk[key] for key in ("crv", "kty", "x", "y")}
    canonical = json.dumps(parts, separators=(",", ":"), sort_keys=True)
    return _b64(hashlib.sha256(canonical.encode()).digest())


def _problem(response: httpx.Response) -> AcmeError:
    detail = ""
    kind = ""
    try:
        body = response.json()
        if isinstance(body, dict):
            kind = str(body.get("type") or "")
            detail = str(body.get("detail") or "")
    except ValueError:
        detail = response.text[:200]
    message = detail or f"ACME 请求失败（HTTP {response.status_code}）"
    if kind:
        message = f"{message} [{kind.rsplit(':', 1)[-1]}]"
    return AcmeError(message, kind=kind, status=response.status_code)


def _challenge_error(authz: dict[str, Any]) -> str:
    for item in authz.get("challenges") or []:
        error = item.get("error") or {}
        if isinstance(error, dict) and error.get("detail"):
            return str(error["detail"])
    return ""


# ---------------------------------------------------------------------------
# queue + renewer
# ---------------------------------------------------------------------------

QUEUE_LIMIT = 200


@dataclass(slots=True)
class QueuedRequest:
    host: str
    state: str  # pending | running | done | error
    message: str
    requested_at: str
    updated_at: str

    def to_public(self) -> dict[str, str]:
        return {
            "host": self.host,
            "state": self.state,
            "message": self.message,
            "requested_at": self.requested_at,
            "updated_at": self.updated_at,
        }


class IssueQueue:
    """A directory of one file per host: the control plane writes, the gateway acts.

    Two processes, one filesystem. The alternative — an HTTP call from the control
    plane into the gateway — would need its own authentication and would break when
    the gateway is down; a file is durable, inspectable, and survives a restart of
    either side.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def _path(self, host: str) -> Path:
        return self.directory / (tls_mod.filename_for(host) + ".json")

    def request(self, host: str) -> QueuedRequest:
        host = tls_mod.normalise(host)
        if not host:
            raise AcmeError("域名不能为空")
        self.directory.mkdir(parents=True, exist_ok=True)
        existing = self.get(host)
        if existing is not None and existing.state in {"pending", "running"}:
            return existing
        now = datetime.now(UTC).isoformat()
        record = QueuedRequest(
            host=host, state="pending", message="", requested_at=now, updated_at=now
        )
        self._write(record)
        return record

    def _write(self, record: QueuedRequest) -> None:
        path = self._path(record.host)
        tmp = path.with_suffix(".tmp")
        payload = json.dumps(record.to_public(), ensure_ascii=False, indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def update(self, host: str, *, state: str, message: str = "") -> None:
        current = self.get(host)
        now = datetime.now(UTC).isoformat()
        record = QueuedRequest(
            host=tls_mod.normalise(host),
            state=state,
            message=message[:400],
            requested_at=current.requested_at if current else now,
            updated_at=now,
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self._write(record)

    def get(self, host: str) -> QueuedRequest | None:
        path = self._path(host)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        return QueuedRequest(
            host=str(data.get("host") or ""),
            state=str(data.get("state") or "pending"),
            message=str(data.get("message") or ""),
            requested_at=str(data.get("requested_at") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def pending(self) -> list[QueuedRequest]:
        if not self.directory.is_dir():
            return []
        items: list[QueuedRequest] = []
        for path in sorted(self.directory.glob("*.json"))[:QUEUE_LIMIT]:
            record = self.get(path.stem)
            if record is not None and record.state == "pending":
                items.append(record)
        return items

    def listing(self) -> list[dict[str, str]]:
        if not self.directory.is_dir():
            return []
        rows: list[dict[str, str]] = []
        for path in sorted(self.directory.glob("*.json")):
            record = self.get(path.stem)
            if record is not None:
                rows.append(record.to_public())
        return rows[:QUEUE_LIMIT]

    def forget(self, host: str) -> bool:
        try:
            self._path(host).unlink()
            return True
        except OSError:
            return False


class Renewer:
    """Keeps every routed host covered, in the background of the gateway process."""

    def __init__(
        self,
        store: tls_mod.CertStore,
        queue: IssueQueue,
        *,
        directory_url: str,
        email: str,
        renew_days: int = 30,
        interval: float = 300.0,
        max_per_tick: int = 4,
        poll_timeout: float = 180.0,
        enabled: bool = True,
    ) -> None:
        self.store = store
        self.queue = queue
        self.directory_url = directory_url
        self.email = email
        self.renew_days = renew_days
        self.interval = interval
        self.max_per_tick = max_per_tick
        self.poll_timeout = poll_timeout
        self.enabled = enabled
        self._last: dict[str, str] = {}

    def client(self) -> AcmeClient:
        return AcmeClient(
            self.directory_url,
            email=self.email,
            account_key=self.store.directory / "account.key",
            challenge=Challenge(self.store.directory / "challenges"),
            poll_timeout=self.poll_timeout,
        )

    async def run(self) -> None:
        """Never raises: a certificate problem must not take the proxy down."""
        if not self.enabled:
            return
        await asyncio.sleep(15)
        while True:
            try:
                await self.tick()
            except Exception as exc:  # noqa: BLE001 - logged, then retried next round
                logger.warning("acme tick failed: %s", exc)
            await asyncio.sleep(self.interval)

    async def tick(self) -> list[dict[str, object]]:
        results: list[dict[str, object]] = []
        budget = self.max_per_tick

        for record in self.queue.pending():
            if budget <= 0:
                break
            budget -= 1
            results.append(await self.issue(record.host, reason="requested"))

        if budget <= 0:
            return results

        for host in self._stale_hosts():
            if budget <= 0:
                break
            budget -= 1
            results.append(await self.issue(host, reason="renewal"))
        return results

    def _stale_hosts(self) -> list[str]:
        """Project hosts with no usable certificate, or one expiring soon."""
        from app.core.config import settings
        from app.gateway.routes import table as route_table

        suffix = tls_mod.normalise(settings.domain_suffix)
        stale: list[str] = []
        for route in route_table.snapshot():
            host = tls_mod.normalise(route.host)
            if not suffix or not (host == suffix or host.endswith("." + suffix)):
                continue  # a custom domain is issued only on explicit request
            cert = self.store.resolve(host)
            if cert is not None and not cert.expired and cert.days_left > self.renew_days:
                continue
            stale.append(host)
        return sorted(set(stale))

    async def issue(self, host: str, *, reason: str = "requested") -> dict[str, object]:
        host = tls_mod.normalise(host)
        self.queue.update(host, state="running", message="")
        try:
            chain, key = await self.client().issue([host])
            record = self.store.store(host, chain, key, source="acme")
        except AcmeError as exc:
            message = str(exc)
            self.queue.update(host, state="error", message=message)
            logger.warning("acme issue failed for %s: %s", host, message)
            return {"host": host, "ok": False, "message": message, "reason": reason}
        except Exception as exc:  # noqa: BLE001 - network, disk, crypto: all reportable
            message = f"{exc.__class__.__name__}: {exc}"
            self.queue.update(host, state="error", message=message)
            logger.warning("acme issue failed for %s: %s", host, message)
            return {"host": host, "ok": False, "message": message, "reason": reason}

        self.queue.update(host, state="done", message=f"有效期至 {record.not_after.date()}")
        logger.info("acme issued %s (%s days)", host, record.days_left)
        return {
            "host": host,
            "ok": True,
            "message": f"{record.issuer} · {record.not_after.date()}",
            "reason": reason,
            "days_left": record.days_left,
        }
