"""Token auth + role gates.

Design constraints:

* **Off by default.** A local workbench behind ``127.0.0.1`` should not make you mint
  credentials before you can click "create app". ``WB_AUTH_ENABLED=false`` means every
  request is an anonymous admin.
* **Tokens in env, not in the database.** Stateless, restart-safe, and it keeps the
  "no migration required" property of the rest of the platform.
* **Roles are coarse on purpose.** ``viewer`` (read), ``editor`` (no destructive
  verbs), ``admin`` (everything). Anything finer belongs to a real IdP, not here.

Token file format: ``WB_AUTH_TOKENS="tok1:alice:admin,tok2:bob:viewer"``. A bare
``WB_AUTH_TOKENS="secret"`` becomes an admin token named ``operator``.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.core.config import settings
from app.core.errors import ForbiddenError, UnauthorizedError

ROLE_RANK = {"viewer": 1, "editor": 2, "admin": 3}

# paths that must stay reachable before the client has a token
PUBLIC_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}
PUBLIC_PREFIXES = ("/api/v1/system/health", "/api/v1/system/settings", "/static", "/favicon")


@dataclass(frozen=True, slots=True)
class Principal:
    name: str
    role: str = "admin"
    anonymous: bool = False

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def may_write(self) -> bool:
        return ROLE_RANK.get(self.role, 0) >= ROLE_RANK["editor"]

    def may_destroy(self) -> bool:
        return self.is_admin

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "role": self.role, "anonymous": self.anonymous}


ANONYMOUS_ADMIN = Principal(name="anonymous", role="admin", anonymous=True)


def parse_tokens(raw: str | None = None) -> dict[str, Principal]:
    source = (settings.auth_tokens if raw is None else raw) or ""
    tokens: dict[str, Principal] = {}
    for entry in source.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        token = parts[0].strip()
        if not token:
            continue
        name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "operator"
        role = parts[2].strip().lower() if len(parts) > 2 and parts[2].strip() else "admin"
        if role not in ROLE_RANK:
            role = "admin"
        tokens[token] = Principal(name=name, role=role)
    if not tokens and settings.auth_bootstrap_token:
        tokens[settings.auth_bootstrap_token] = Principal(name="operator", role="admin")
    return tokens


def extract_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    explicit = request.headers.get("x-workbench-token")
    if explicit:
        return explicit.strip()
    # EventSource cannot set headers; the SSE endpoint accepts ?token=
    return (request.query_params.get("token") or "").strip()


def resolve(request: Request) -> Principal:
    """Identity for this request. Anonymous admin when auth is disabled."""
    if not settings.auth_enabled:
        return ANONYMOUS_ADMIN
    token = extract_token(request)
    principal = parse_tokens().get(token) if token else None
    if principal is None:
        raise UnauthorizedError("缺少或无效的访问令牌", details={"scheme": "Bearer"})
    return principal


def get_principal(request: Request) -> Principal:
    """FastAPI dependency — relies on the middleware having resolved the identity."""
    principal = getattr(request.state, "principal", None)
    return principal if isinstance(principal, Principal) else ANONYMOUS_ADMIN


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def check_method(principal: Principal, method: str) -> None:
    """Coarse RBAC: viewer reads, editor mutates, admin destroys."""
    verb = method.upper()
    if verb in {"GET", "HEAD", "OPTIONS"}:
        return
    if verb == "DELETE":
        if not principal.may_destroy():
            raise ForbiddenError(f"角色 {principal.role} 不能执行删除操作")
        return
    if not principal.may_write():
        raise ForbiddenError(f"角色 {principal.role} 为只读，不能修改资源")


def can_access(principal: Principal, owner_id: str | None) -> bool:
    """Owners see their own projects; admins see everything."""
    if principal.is_admin or not owner_id:
        return True
    return owner_id == principal.name
