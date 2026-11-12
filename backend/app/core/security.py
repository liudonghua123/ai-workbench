"""Identity resolution and authorisation.

The flow, per request:

1. ``WB_AUTH_ENABLED=false`` → an anonymous admin. A control plane on
   ``127.0.0.1`` should not require credentials before you can click "create app".
2. Otherwise a bearer token is either a **session** (``wbs_…``, hashed at rest in
   the ``sessions`` table) or a **bootstrap token** from ``WB_AUTH_TOKENS``.

A session resolves into a :class:`Principal` carrying the *effective* permission
set for one organization: the membership role, plus any project-scoped
``RoleBinding`` grants. Authority is therefore recomputed on every request —
removing a role takes effect immediately instead of at the next login.

Design constraints, kept from the previous version on purpose:

* **Tokens in env still work.** Upgrading should not lock the operator out of
  their own workbench; ``WB_AUTH_TOKENS`` maps onto the builtin roles.
* **One rule set for REST and MCP.** Resolution happens in middleware, so the
  FastMCP app mounted at ``/mcp`` is covered by the same policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from fastapi import Request
from sqlmodel import Session, select

from app.core import passwords
from app.core.config import settings
from app.core.db import session_scope
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core import permissions as perms
from app.models import (
    IdentityProvider,
    Membership,
    MemberStatus,
    Organization,
    OrgStatus,
    Project,
    Role,
    RoleBinding,
    Session as SessionModel,
    User,
    UserStatus,
)

# Legacy role names kept so old configs keep working.
LEGACY_ROLE_MAP = {"viewer": "viewer", "editor": "developer", "admin": "admin"}

# paths that must stay reachable before the client has a token
PUBLIC_PATHS = {"/", "/docs", "/redoc", "/openapi.json"}
PUBLIC_PREFIXES = (
    "/api/v1/system/health",
    "/api/v1/system/settings",
    "/api/v1/auth/",
    "/static",
    "/favicon",
)

# header used by API clients / the SPA to pin the active organization
ORG_HEADER = "x-org-id"


@dataclass(frozen=True, slots=True)
class OrgRef:
    id: str
    slug: str
    name: str
    role_key: str


@dataclass(frozen=True, slots=True)
class Principal:
    """Everything needed to answer "may this caller do X to Y"."""

    name: str
    role: str = "admin"
    anonymous: bool = False
    user_id: str = ""
    email: str = ""
    org_id: str = ""
    org_slug: str = ""
    permissions: frozenset[str] = frozenset()
    # project_id -> extra permissions granted by a RoleBinding
    grants: dict[str, frozenset[str]] = field(default_factory=dict)
    orgs: tuple[OrgRef, ...] = ()
    is_platform_admin: bool = False
    auth_method: str = "none"  # none | session | bootstrap
    session_id: str = ""
    # data scope of the role this session is pinned to: "all" | "own"
    data_scope: str = "all"

    # -- coarse checks kept for older call sites -------------------------
    @property
    def is_admin(self) -> bool:
        return self.is_platform_admin or "org:delete" in self.permissions

    def may_write(self) -> bool:
        return self.has("project:update")

    def may_destroy(self) -> bool:
        return self.has("project:delete")

    def sees_all_projects(self) -> bool:
        """True when the data scope is the whole organization."""
        return self.anonymous or self.is_platform_admin or self.data_scope == "all"

    def reaches(
        self,
        *,
        owner_id: str = "",
        org_id: str = "",
        project_id: str = "",
    ) -> bool:
        """Data scope: may this caller see *this row*?

        Three gates, in order:

        1. **Tenant.** Another organization is out of reach even for a super
           admin — being owner of A must not mean reading B.
        2. **Scope.** ``all`` short-circuits; ``own`` falls through to ownership.
        3. **Ownership or grant.** Owned rows are yours by id, by the legacy
           email/name columns, or because a ``RoleBinding`` names you as grantee.
        """
        if org_id and self.org_id and org_id != self.org_id and not self.is_platform_admin:
            return False
        if self.sees_all_projects():
            return True
        if project_id and project_id in self.grants:
            return True
        owners = {self.user_id, self.email, self.name} - {""}
        return bool(owner_id) and owner_id in owners

    # -- real checks ------------------------------------------------------
    def has(self, permission: str, project_id: str | None = None) -> bool:
        if not permission:
            return True
        if self.anonymous or self.is_platform_admin:
            return True
        if "*" in self.permissions or permission in self.permissions:
            return True
        if project_id:
            extra = self.grants.get(project_id)
            if extra and permission in extra:
                return True
        return False

    def has_any(self, *candidates: str) -> bool:
        return any(self.has(item) for item in candidates)

    def require(self, permission: str, project_id: str | None = None, detail: str = "") -> None:
        if not self.has(permission, project_id):
            raise ForbiddenError(detail or f"缺少权限 {permission}")

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "role": self.role,
            "anonymous": self.anonymous,
            "user_id": self.user_id,
            "email": self.email,
            "org_id": self.org_id,
            "org_slug": self.org_slug,
            "is_platform_admin": self.is_platform_admin,
            "auth_method": self.auth_method,
            "data_scope": self.data_scope,
            "sees_all_projects": self.sees_all_projects(),
            "permissions": sorted(self.permissions),
            "projects_with_grants": sorted(self.grants),
            "organizations": [
                {"id": o.id, "slug": o.slug, "name": o.name, "role_key": o.role_key}
                for o in self.orgs
            ],
        }


ANONYMOUS_ADMIN = Principal(
    name="anonymous",
    role="owner",
    anonymous=True,
    permissions=perms.expand(["*"]),
    is_platform_admin=True,
    auth_method="none",
)


# --------------------------------------------------------------------------
# bootstrap tokens (WB_AUTH_TOKENS)
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BootstrapToken:
    token: str
    name: str
    role_key: str


def parse_tokens(raw: str | None = None) -> dict[str, BootstrapToken]:
    """``tok1:alice:admin,tok2:bob:viewer`` — bare ``secret`` means operator/admin."""
    source = (settings.auth_tokens if raw is None else raw) or ""
    tokens: dict[str, BootstrapToken] = {}
    for entry in source.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":")
        token = parts[0].strip()
        if not token:
            continue
        name = parts[1].strip() if len(parts) > 1 and parts[1].strip() else "operator"
        legacy = parts[2].strip().lower() if len(parts) > 2 and parts[2].strip() else "admin"
        tokens[token] = BootstrapToken(token=token, name=name,
                                       role_key=LEGACY_ROLE_MAP.get(legacy, "admin"))
    if not tokens and settings.auth_bootstrap_token:
        tokens[settings.auth_bootstrap_token] = BootstrapToken(
            token=settings.auth_bootstrap_token, name="operator", role_key="admin"
        )
    return tokens


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------


def extract_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    explicit = request.headers.get("x-workbench-token")
    if explicit:
        return explicit.strip()
    # EventSource cannot set headers; the SSE endpoint accepts ?token=
    return (request.query_params.get("token") or "").strip()


def _active_orgs(session: Session, user_id: str) -> list[tuple[Organization, Membership]]:
    rows = session.exec(
        select(Membership).where(
            Membership.user_id == user_id, Membership.status == MemberStatus.ACTIVE
        )
    ).all()
    pairs: list[tuple[Organization, Membership]] = []
    for membership in rows:
        org = session.get(Organization, membership.org_id)
        if org and org.status == OrgStatus.ACTIVE:
            pairs.append((org, membership))
    pairs.sort(key=lambda pair: (pair[1].role_key != "owner", pair[0].name))
    return pairs


def effective_permissions(
    session: Session, org_id: str, role_key: str
) -> frozenset[str]:
    role = session.exec(
        select(Role).where(Role.org_id == org_id, Role.key == role_key)
    ).first()
    if role is not None:
        return perms.expand(role.permissions)
    return perms.role_permissions(role_key)


def effective_scope(session: Session, org_id: str, role_key: str) -> str:
    """Data scope of one role inside one organization.

    Read from the role row so the UI can change it without a deploy; falls back
    to the builtin declaration for an org whose roles were never seeded.
    """
    role = session.exec(
        select(Role).where(Role.org_id == org_id, Role.key == role_key)
    ).first()
    if role is not None:
        return perms.resolve_scope(role.data_scope)
    return perms.role_scope(role_key)


def _project_grants(session: Session, user_id: str) -> dict[str, frozenset[str]]:
    rows = session.exec(select(RoleBinding).where(RoleBinding.user_id == user_id)).all()
    grants: dict[str, frozenset[str]] = {}
    for row in rows:
        if not row.scope_id:
            continue
        granted = perms.expand(row.permissions)
        if row.role_key:
            granted = granted | perms.role_permissions(row.role_key)
        grants[row.scope_id] = grants.get(row.scope_id, frozenset()) | granted
    return grants


def principal_from_session(session: Session, row: SessionModel, request: Request | None = None):
    user = session.get(User, row.user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise UnauthorizedError("账户不可用")

    pairs = _active_orgs(session, user.id)
    if not pairs:
        # authenticated but belongs nowhere: still a valid identity, no authority
        return Principal(
            name=user.name or user.email,
            role="none",
            user_id=user.id,
            email=user.email,
            permissions=frozenset(),
            auth_method="session",
            session_id=row.id,
        )

    wanted = ""
    if request is not None:
        wanted = (request.headers.get(ORG_HEADER) or "").strip()
    chosen: tuple[Organization, Membership] | None = None
    if wanted:
        chosen = next(
            (pair for pair in pairs if wanted in (pair[0].id, pair[0].slug)), None
        )
    if chosen is None and row.org_id:
        chosen = next((pair for pair in pairs if pair[0].id == row.org_id), None)
    if chosen is None:
        chosen = pairs[0]

    org, membership = chosen
    permissions = effective_permissions(session, org.id, membership.role_key)
    scope = effective_scope(session, org.id, membership.role_key)
    if user.is_platform_admin:
        permissions = perms.expand(["*"])
        scope = "all"

    return Principal(
        name=user.name or user.email or user.id,
        role=membership.role_key,
        user_id=user.id,
        email=user.email,
        org_id=org.id,
        org_slug=org.slug,
        permissions=permissions,
        grants=_project_grants(session, user.id),
        orgs=tuple(
            OrgRef(id=o.id, slug=o.slug, name=o.name, role_key=m.role_key) for o, m in pairs
        ),
        is_platform_admin=user.is_platform_admin,
        auth_method="session",
        session_id=row.id,
        data_scope=scope,
    )


def _bootstrap_principal(token: BootstrapToken, session: Session) -> Principal:
    """Env tokens act as a member of the default org, with the mapped role."""
    org = session.exec(select(Organization).order_by(Organization.created_at)).first()  # type: ignore[attr-defined]
    org_id = org.id if org else ""
    org_slug = org.slug if org else ""
    scope = effective_scope(session, org_id, token.role_key) if org else "all"
    return Principal(
        name=token.name,
        role=token.role_key,
        org_id=org_id,
        org_slug=org_slug,
        permissions=perms.role_permissions(token.role_key),
        orgs=(OrgRef(id=org.id, slug=org.slug, name=org.name, role_key=token.role_key),)
        if org
        else (),
        auth_method="bootstrap",
        # an operator token is deployment plumbing: it is not "a user's own projects"
        data_scope=scope,
    )


def resolve(request: Request) -> Principal:
    """Identity for this request. Raises 401 when auth is on and nothing matches."""
    if not settings.auth_enabled:
        return ANONYMOUS_ADMIN

    token = extract_token(request)
    if not token:
        raise UnauthorizedError("缺少访问令牌", details={"scheme": "Bearer"})

    with session_scope() as session:
        bootstrap = parse_tokens().get(token)
        if bootstrap is not None:
            return _bootstrap_principal(bootstrap, session)

        row = session.exec(
            select(SessionModel).where(SessionModel.token_hash == passwords.token_hash(token))
        ).first()
        if row is None:
            raise UnauthorizedError("令牌无效", details={"scheme": "Bearer"})
        now = datetime.now(timezone.utc)
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if row.revoked_at is not None or expires <= now:
            raise UnauthorizedError("会话已过期，请重新登录", details={"reason": "expired"})

        # cheap sliding activity marker; write at most once a minute
        if now.timestamp() - row.last_seen_at.replace(tzinfo=timezone.utc).timestamp() > 60:
            row.last_seen_at = now
            session.add(row)

        return principal_from_session(session, row, request)


def get_principal(request: Request) -> Principal:
    """FastAPI dependency — the middleware has already resolved the identity."""
    principal = getattr(request.state, "principal", None)
    return principal if isinstance(principal, Principal) else ANONYMOUS_ADMIN


def require(permission: str):
    """Dependency factory for endpoints with a project id in the path."""

    def _dependency(request: Request) -> Principal:
        principal = get_principal(request)
        project_id = request.path_params.get("project_id")
        principal.require(permission, project_id)
        return principal

    return _dependency


# --------------------------------------------------------------------------
# helpers used by the routers
# --------------------------------------------------------------------------


def is_public(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def can_access(
    principal: Principal,
    owner_id: str | None,
    org_id: str | None = None,
    project_id: str | None = None,
) -> bool:
    """Project visibility, kept for call sites that already hold the row.

    The rule itself lives in :meth:`Principal.reaches` — being owner of one
    organization must not mean reading another, and a role scoped to ``own``
    only reaches rows the caller owns or was granted.
    """
    return principal.reaches(
        owner_id=owner_id or "", org_id=org_id or "", project_id=project_id or ""
    )


def require_project(session: Session, principal: Principal, project_id: str) -> Project:
    """Resolve a project this principal may reach, or 404.

    The single implementation behind three call sites that must never disagree:
    the REST dependency (``api/deps.py``), the MCP tool guard and the agent
    runtime's entry check. Imports ``scaffold`` lazily because that module
    deliberately knows nothing about authentication.
    """
    from app.core.errors import NotFoundError
    from app.services import scaffold

    project = scaffold.get_project(session, project_id)
    if not principal.reaches(
        owner_id=project.owner_id, org_id=project.org_id, project_id=project.id
    ):
        # 404, not 403: a 403 would confirm the id exists elsewhere
        raise NotFoundError(f"项目不存在: {project_id}")
    return project


def sso_enabled() -> bool:
    """At least one enabled provider that is not the local password store."""
    with session_scope() as session:
        row = session.exec(
            select(IdentityProvider).where(
                IdentityProvider.enabled == True,  # noqa: E712
                IdentityProvider.kind != "local",
            )
        ).first()
        return row is not None
