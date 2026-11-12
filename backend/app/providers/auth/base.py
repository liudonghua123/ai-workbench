"""Identity provider SPI.

Each provider turns *one* authorization-code round trip into an
:class:`ExternalProfile`. Nothing else in the codebase knows whether the user came
from CAS, OIDC, WeCom or DingTalk — the session, the JIT provisioning and the
audit trail are shared.

The contract is deliberately narrow::

    authorize_url(ctx) -> str          # where to send the browser
    exchange(code, ctx) -> ExternalProfile   # server-side code-for-profile
    probe() -> dict                    # "is this connection sane" for the UI

Adding a sixth IdP means one class and one registry entry, in the same spirit as
adding a storage or deploy provider.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import httpx

from app.models import IdentityProvider

logger = logging.getLogger("workbench.auth")

HTTP_TIMEOUT = 12.0


class AuthProviderError(RuntimeError):
    """Raised when the upstream IdP misbehaves, with a message safe to show."""


@dataclass(slots=True)
class AuthorizeContext:
    redirect_uri: str
    state: str
    code_challenge: str = ""
    nonce: str = ""
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ExternalProfile:
    subject: str
    email: str = ""
    name: str = ""
    avatar: str = ""
    tenant: str = ""
    groups: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def display(self) -> str:
        return self.name or self.email or self.subject


def _dig(payload: dict[str, Any], path: str, default: Any = "") -> Any:
    """Read ``a.b.c`` (or ``a[0].b``) out of a nested JSON document."""
    current: Any = payload
    for chunk in path.replace("[", ".").replace("]", "").split("."):
        if not chunk:
            continue
        if isinstance(current, list):
            try:
                current = current[int(chunk)]
            except (ValueError, IndexError):
                return default
        elif isinstance(current, dict):
            current = current.get(chunk, default)
        else:
            return default
        if current is None:
            return default
    return current


class AuthProvider:
    """Base class — subclasses implement at least ``authorize_url``/``exchange``."""

    kind = "base"
    #: config keys that must be present before the connection can be enabled
    required: tuple[str, ...] = ()
    #: secrets that must be present
    required_secret: tuple[str, ...] = ()
    #: how the browser completes the round trip
    mode = "redirect"

    def __init__(self, row: IdentityProvider):
        self.row = row
        self.config: dict[str, Any] = dict(row.config or {})
        self.secret: dict[str, Any] = dict(row.secret or {})

    # -- configuration health -------------------------------------------
    def missing(self) -> list[str]:
        gaps = [key for key in self.required if not str(self.config.get(key) or "").strip()]
        gaps += [
            key for key in self.required_secret if not str(self.secret.get(key) or "").strip()
        ]
        return gaps

    def ready(self) -> bool:
        return not self.missing()

    # -- flow ------------------------------------------------------------
    def authorize_url(self, ctx: AuthorizeContext) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def exchange(self, code: str, ctx: AuthorizeContext) -> ExternalProfile:  # pragma: no cover
        raise NotImplementedError

    def probe(self) -> dict[str, Any]:
        gaps = self.missing()
        return {
            "ok": not gaps,
            "status": "ready" if not gaps else "unconfigured",
            "missing": gaps,
            "message": "配置完整" if not gaps else f"缺少 {', '.join(gaps)}",
        }

    # -- helpers ---------------------------------------------------------
    @staticmethod
    def _client() -> httpx.Client:
        return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)

    def _get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthProviderError(f"无法连接身份源: {exc}") from exc
        return self._json_or_raise(response, url)

    def _post_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.post(url, **kwargs)
        except httpx.HTTPError as exc:
            raise AuthProviderError(f"无法连接身份源: {exc}") from exc
        return self._json_or_raise(response, url)

    @staticmethod
    def _json_or_raise(response: httpx.Response, url: str) -> dict[str, Any]:
        if response.status_code >= 400:
            snippet = response.text[:200].replace("\n", " ")
            raise AuthProviderError(f"{url} 返回 {response.status_code}: {snippet}")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise AuthProviderError(f"{url} 返回的不是 JSON") from exc
        if not isinstance(data, dict):
            raise AuthProviderError(f"{url} 返回结构异常")
        return data

    def describe(self) -> dict[str, Any]:
        return {
            "key": self.row.key,
            "kind": self.kind,
            "name": self.row.name,
            "icon": self.row.icon or self.kind,
            "mode": self.mode,
            "ready": self.ready(),
            "missing": self.missing(),
        }


def authorized_params(*pairs: tuple[str, str]) -> str:
    return urlencode([(key, value) for key, value in pairs if value])
