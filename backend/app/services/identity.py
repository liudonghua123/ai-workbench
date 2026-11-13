"""Identity lifecycle: organizations, JIT provisioning, sessions, SSO round trips.

The rule this module enforces is the one that makes enterprise SSO usable:
**the IdP says who you are, the workbench says what you may do.** A first login
from WeCom creates the user, links the identity, drops them into the organization
the connection belongs to, and gives them the role that connection is configured
for (optionally derived from IdP groups). Second login onwards is a lookup.

Sessions are opaque bearer tokens; only their SHA-256 is stored, so a database
dump cannot be replayed as anyone's session.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlmodel import Session, select

from app.core import passwords
from app.core import permissions as perms
from app.core.config import settings
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from app.core.ids import new_id, slugify
from app.models import (
    AuthRequest,
    Identity,
    IdentityProvider,
    Invitation,
    Membership,
    MemberStatus,
    Organization,
    OrgStatus,
    Role,
    User,
    UserStatus,
)
from app.models import (
    Session as SessionModel,
)
from app.providers import auth as auth_providers
from app.providers.auth import AuthorizeContext, AuthProviderError, ExternalProfile
from app.services import audit

logger = logging.getLogger("workbench.identity")


def utcnow() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# organizations & roles
# --------------------------------------------------------------------------


def ensure_builtin_roles(session: Session, org: Organization) -> list[Role]:
    """Seed the five builtin roles for one organization (idempotent)."""
    existing = {
        row.key: row for row in session.exec(select(Role).where(Role.org_id == org.id)).all()
    }
    created: list[Role] = []
    healed = False
    for spec in perms.BUILTIN_ROLES:
        if spec.key in existing:
            row = existing[spec.key]
            # Rows seeded before data scopes existed carry an empty value; labels
            # ship with the catalog and are refreshed, because a builtin role's
            # description is documentation, not user content. Permissions are
            # *not* touched: those may have been edited deliberately.
            changed = False
            if not row.data_scope:
                row.data_scope = spec.data_scope
                changed = True
            if (row.name, row.description, row.rank) != (spec.name, spec.description, spec.rank):
                row.name, row.description, row.rank = spec.name, spec.description, spec.rank
                changed = True
            if changed:
                session.add(row)
                healed = True
            continue
        role = Role(
            id=new_id("rol"),
            org_id=org.id,
            key=spec.key,
            name=spec.name,
            description=spec.description,
            permissions=list(spec.grants),
            builtin=True,
            rank=spec.rank,
            data_scope=spec.data_scope,
        )
        session.add(role)
        created.append(role)
    if created or healed:
        session.commit()
    return created


def create_organization(
    session: Session,
    *,
    name: str,
    slug: str = "",
    owner: User | None = None,
    description: str = "",
    plan: str = "team",
) -> Organization:
    candidate = slugify(slug or name) or f"org-{new_id('x')[2:8]}"
    if session.exec(select(Organization).where(Organization.slug == candidate)).first():
        raise ConflictError(f"组织标识已存在: {candidate}")
    org = Organization(
        id=new_id("org"),
        slug=candidate,
        name=name.strip() or candidate,
        description=description,
        plan=plan,
        owner_id=owner.id if owner else "",
    )
    session.add(org)
    session.commit()
    session.refresh(org)
    ensure_builtin_roles(session, org)
    if owner is not None:
        session.add(
            Membership(
                id=new_id("mem"),
                org_id=org.id,
                user_id=owner.id,
                role_key="owner",
                status=MemberStatus.ACTIVE,
            )
        )
        session.commit()
        audit.record(
            session, "org.created", actor_id=owner.id, actor_name=owner.name or owner.email,
            org_id=org.id, resource_type="organization", resource_id=org.id,
            detail={"slug": org.slug}, commit=True,
        )
    return org


def default_org(session: Session) -> Organization:
    """The org that legacy projects and bootstrap tokens fall into."""
    org = session.exec(
        select(Organization).where(Organization.slug == settings.auth_default_org)
    ).first()
    if org is not None:
        return org
    org = session.exec(select(Organization).order_by(Organization.created_at)).first()  # type: ignore[attr-defined]
    if org is not None:
        return org
    return create_organization(session, name="Default", slug=settings.auth_default_org or "default")


# --------------------------------------------------------------------------
# users
# --------------------------------------------------------------------------


def user_by_email(session: Session, email: str) -> User | None:
    normalized = (email or "").strip().lower()
    if not normalized:
        return None
    return session.exec(select(User).where(User.email == normalized)).first()


def create_user(
    session: Session,
    *,
    email: str,
    name: str = "",
    password: str | None = None,
    avatar: str = "",
    is_platform_admin: bool = False,
) -> User:
    normalized = (email or "").strip().lower()
    if normalized and user_by_email(session, normalized):
        raise ConflictError(f"邮箱已被使用: {normalized}")
    user = User(
        id=new_id("usr"),
        email=normalized,
        name=(name or normalized.split("@")[0] or "user").strip(),
        avatar=avatar,
        password_hash=passwords.hash_password(password) if password else "",
        is_platform_admin=is_platform_admin,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def set_password(session: Session, user: User, password: str) -> None:
    user.password_hash = passwords.hash_password(password)
    user.updated_at = utcnow()
    session.add(user)
    session.commit()


def membership_for(session: Session, org_id: str, user_id: str) -> Membership | None:
    return session.exec(
        select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
    ).first()


def ensure_membership(
    session: Session, org: Organization, user: User, role_key: str
) -> Membership:
    existing = membership_for(session, org.id, user.id)
    if existing is not None:
        if existing.status != MemberStatus.ACTIVE:
            existing.status = MemberStatus.ACTIVE
            session.add(existing)
            session.commit()
        return existing
    membership = Membership(
        id=new_id("mem"),
        org_id=org.id,
        user_id=user.id,
        role_key=role_key if role_key else "viewer",
        status=MemberStatus.ACTIVE,
    )
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership


def change_role(session: Session, membership: Membership, role_key: str) -> Membership:
    membership.role_key = role_key
    membership.updated_at = utcnow()
    session.add(membership)
    session.commit()
    session.refresh(membership)
    return membership


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------


def create_session(
    session: Session,
    user: User,
    *,
    org_id: str = "",
    ip: str = "",
    user_agent: str = "",
    kind: str = "session",
    ttl_hours: int | None = None,
) -> tuple[str, SessionModel]:
    token = passwords.new_token()
    hours = ttl_hours or settings.auth_session_hours
    row = SessionModel(
        id=new_id("ses"),
        user_id=user.id,
        token_hash=passwords.token_hash(token),
        org_id=org_id,
        kind=kind,
        ip=ip,
        user_agent=(user_agent or "")[:300],
        expires_at=utcnow() + timedelta(hours=hours),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return token, row


def revoke_session(session: Session, session_id: str) -> bool:
    row = session.get(SessionModel, session_id)
    if row is None:
        return False
    row.revoked_at = utcnow()
    session.add(row)
    session.commit()
    return True


def revoke_sessions_for(session: Session, user_id: str) -> int:
    rows = session.exec(
        select(SessionModel).where(
            SessionModel.user_id == user_id, SessionModel.revoked_at == None  # noqa: E711
        )
    ).all()
    now = utcnow()
    for row in rows:
        row.revoked_at = now
        session.add(row)
    session.commit()
    return len(rows)


def list_sessions(session: Session, user_id: str) -> list[SessionModel]:
    return list(
        session.exec(
            select(SessionModel)
            .where(SessionModel.user_id == user_id, SessionModel.revoked_at == None)  # noqa: E711
            .order_by(SessionModel.created_at.desc())  # type: ignore[attr-defined]
        ).all()
    )


# --------------------------------------------------------------------------
# local password login / signup
# --------------------------------------------------------------------------


def login_with_password(
    session: Session, email: str, password: str, *, ip: str = "", user_agent: str = ""
) -> tuple[str, User, Organization]:
    user = user_by_email(session, email)
    if user is None or not passwords.verify_password(password, user.password_hash):
        audit.record(
            session, "auth.login_failed", actor_name=(email or "")[:80], status="denied",
            detail={"method": "password"}, ip=ip, commit=True,
        )
        raise UnauthorizedError("邮箱或密码不正确")
    if user.status != UserStatus.ACTIVE:
        raise ForbiddenError("账户已被停用")

    membership = session.exec(
        select(Membership).where(
            Membership.user_id == user.id, Membership.status == MemberStatus.ACTIVE
        )
    ).first()
    org = session.get(Organization, membership.org_id) if membership else default_org(session)

    token, _row = create_session(session, user, org_id=org.id, ip=ip, user_agent=user_agent)
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    audit.record(
        session, "auth.login", actor_id=user.id, actor_name=user.name,
        org_id=org.id, detail={"method": "password"}, ip=ip, commit=True,
    )
    return token, user, org


def signup(
    session: Session,
    *,
    email: str,
    password: str,
    name: str = "",
    org_name: str = "",
    ip: str = "",
    user_agent: str = "",
) -> tuple[str, User, Organization]:
    if not settings.auth_allow_signup:
        raise ForbiddenError("本实例已关闭自助注册，请联系管理员")
    if len(password or "") < 8:
        raise ForbiddenError("密码至少 8 位")
    user = create_user(session, email=email, name=name, password=password)
    if org_name:
        org = create_organization(session, name=org_name, owner=user)
    else:
        org = default_org(session)
        ensure_membership(session, org, user, "developer")
    token, _row = create_session(session, user, org_id=org.id, ip=ip, user_agent=user_agent)
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    audit.record(
        session, "auth.login", actor_id=user.id, actor_name=user.name, org_id=org.id,
        detail={"method": "signup"}, ip=ip, commit=True,
    )
    return token, user, org


# --------------------------------------------------------------------------
# SSO
# --------------------------------------------------------------------------


def provider_by_key(session: Session, key: str, *, enabled_only: bool = True) -> IdentityProvider:
    row = session.exec(select(IdentityProvider).where(IdentityProvider.key == key)).first()
    if row is None:
        raise NotFoundError(f"身份源不存在: {key}")
    if enabled_only and not row.enabled:
        raise ForbiddenError(f"身份源未启用: {key}")
    return row


def enabled_providers(session: Session, org_id: str = "") -> list[IdentityProvider]:
    rows = session.exec(
        select(IdentityProvider).where(IdentityProvider.enabled == True)  # noqa: E712
    ).all()
    # org-specific connections first, then platform-wide ones
    visible = [row for row in rows if not row.org_id or not org_id or row.org_id == org_id]
    visible.sort(key=lambda row: (row.org_id == "", row.name))
    return visible


def start_sso(
    session: Session,
    provider: IdentityProvider,
    *,
    redirect_uri: str,
    return_to: str = "/",
) -> tuple[AuthRequest, str]:
    verifier, challenge = passwords.new_pkce_pair()
    row = AuthRequest(
        id=new_id("arq"),
        state=passwords.new_state(),
        provider_key=provider.key,
        org_id=provider.org_id,
        redirect_uri=redirect_uri,
        return_to=(return_to or "/")[:500],
        code_verifier=verifier,
        nonce=passwords.new_state()[:16],
        expires_at=utcnow() + timedelta(minutes=settings.auth_state_ttl),
    )
    session.add(row)
    session.commit()
    session.refresh(row)

    ctx = AuthorizeContext(
        redirect_uri=redirect_uri,
        state=row.state,
        code_challenge=challenge,
        nonce=row.nonce,
        extra={"code_verifier": verifier},
    )
    url = auth_providers.build(provider).authorize_url(ctx)
    audit.record(
        session, "auth.sso_start", org_id=provider.org_id, status="ok",
        detail={"provider": provider.key, "return_to": row.return_to}, commit=True,
    )
    return row, url


def _role_from_groups(provider: IdentityProvider, profile: ExternalProfile) -> str:
    """IdP group → role, picking the highest-ranked match."""
    mapping: dict[str, str] = {}
    raw = provider.config.get("role_map") if isinstance(provider.config, dict) else None
    if isinstance(raw, dict):
        mapping = {str(key): str(value) for key, value in raw.items()}
    matched = [mapping[group] for group in profile.groups if group in mapping]
    if not matched:
        return provider.default_role_key or "viewer"
    return max(matched, key=lambda key: perms.BUILTIN_BY_KEY.get(key).rank
               if key in perms.BUILTIN_BY_KEY else 0)


def _domain_allowed(provider: IdentityProvider, email: str) -> bool:
    allowed = [item.lower().lstrip("@") for item in (provider.allowed_domains or [])]
    if not allowed:
        return True
    domain = email.split("@")[-1].lower() if "@" in email else ""
    return domain in allowed


def provision(
    session: Session, provider: IdentityProvider, profile: ExternalProfile
) -> tuple[User, Membership | None, bool]:
    """Link or create the user, then make sure they are a member.

    Returns (user, membership, created).
    """
    if not profile.email:
        # addressable fallback: CAS often returns only a uid
        profile.email = (
            f"{profile.subject}@{(provider.config or {}).get('email_domain') or 'sso.local'}"
        )
    if not _domain_allowed(provider, profile.email):
        raise ForbiddenError(f"邮箱域不在允许范围内: {profile.email}")

    identity = session.exec(
        select(Identity).where(
            Identity.provider_key == provider.key, Identity.subject == profile.subject
        )
    ).first()

    created = False
    user: User | None = None
    if identity is not None:
        user = session.get(User, identity.user_id)
    if user is None:
        user = user_by_email(session, profile.email)
        if user is None:
            if not provider.auto_create_users:
                raise ForbiddenError("该身份源未开启自动开户，请联系管理员邀请")
            user = create_user(
                session, email=profile.email, name=profile.display(), avatar=profile.avatar
            )
            created = True

    if identity is None:
        identity = Identity(
            id=new_id("idt"),
            user_id=user.id,
            provider_key=provider.key,
            kind=provider.kind,
            subject=profile.subject,
            email=profile.email,
            display_name=profile.display(),
            raw=profile.raw,
        )
    else:
        identity.email = profile.email or identity.email
        identity.display_name = profile.display() or identity.display_name
        identity.raw = profile.raw
    identity.last_login_at = utcnow()
    session.add(identity)

    membership: Membership | None = None
    if provider.auto_join:
        org = (
            session.get(Organization, provider.org_id)
            if provider.org_id
            else default_org(session)
        )
        if org is None or org.status != OrgStatus.ACTIVE:
            raise ForbiddenError("身份源所属组织不可用")
        existing = membership_for(session, org.id, user.id)
        role_key = _role_from_groups(provider, profile)
        # ensure_membership never rewrites an existing role: an admin who logs in
        # through an IdP mapped to "viewer" must not be demoted by accident
        membership = ensure_membership(session, org, user, role_key)
        if existing is None:
            session.commit()
    session.commit()
    session.refresh(user)
    return user, membership, created


def complete_sso(
    session: Session,
    *,
    state: str,
    code: str,
    ip: str = "",
    user_agent: str = "",
) -> tuple[str, User, Organization, str]:
    """Verify state, exchange the code, provision, issue a session."""
    row = session.exec(select(AuthRequest).where(AuthRequest.state == state)).first()
    if row is None:
        raise UnauthorizedError("登录状态无效或已被使用，请重新发起登录")
    expires = _aware(row.expires_at)
    if expires and expires <= utcnow():
        session.delete(row)
        session.commit()
        raise UnauthorizedError("登录状态已过期，请重试")

    provider = provider_by_key(session, row.provider_key)
    ctx = AuthorizeContext(
        redirect_uri=row.redirect_uri,
        state=row.state,
        nonce=row.nonce,
        extra={"code_verifier": row.code_verifier},
    )
    try:
        profile = auth_providers.build(provider).exchange(code, ctx)
        user, membership, created = provision(session, provider, profile)
    except AuthProviderError as exc:
        session.delete(row)
        session.commit()
        audit.record(
            session, "auth.sso_failed", org_id=row.org_id, status="error",
            detail={"provider": row.provider_key, "error": str(exc)[:300]}, ip=ip, commit=True,
        )
        raise UnauthorizedError(str(exc)) from exc

    org_id = membership.org_id if membership else (provider.org_id or "")
    org = session.get(Organization, org_id) if org_id else default_org(session)
    if org is None:
        raise ForbiddenError("账户尚未加入任何组织")

    token, _session_row = create_session(
        session, user, org_id=org.id, ip=ip, user_agent=user_agent
    )
    user.last_login_at = utcnow()
    session.add(user)
    session.commit()
    session.delete(row)  # one-shot: replaying the callback cannot mint a second session
    session.commit()

    audit.record(
        session, "auth.login", actor_id=user.id, actor_name=user.name, org_id=org.id,
        detail={"method": row.provider_key, "created_user": created,
                "subject": profile.subject}, ip=ip, commit=True,
    )
    return token, user, org, row.return_to


# --------------------------------------------------------------------------
# invitations
# --------------------------------------------------------------------------


def invite(
    session: Session,
    org: Organization,
    *,
    email: str,
    role_key: str,
    invited_by: str = "",
    ttl_days: int = 7,
) -> tuple[Invitation, str]:
    normalized = (email or "").strip().lower()
    if not normalized or "@" not in normalized:
        raise ForbiddenError("请填写有效邮箱")
    token = passwords.new_token("wbi")
    row = Invitation(
        id=new_id("inv"),
        org_id=org.id,
        email=normalized,
        role_key=role_key,
        token_hash=passwords.token_hash(token),
        invited_by=invited_by,
        expires_at=utcnow() + timedelta(days=ttl_days),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "member.invited", actor_name=invited_by, org_id=org.id,
        resource_type="invitation", resource_id=row.id,
        detail={"email": normalized, "role_key": role_key}, commit=True,
    )
    return row, token


def accept_invitation(session: Session, token: str, user: User) -> Membership:
    hashed = passwords.token_hash(token)
    row = session.exec(select(Invitation).where(Invitation.token_hash == hashed)).first()
    if row is None or row.status != "pending":
        raise NotFoundError("邀请不存在或已失效")
    expires = _aware(row.expires_at)
    if expires and expires <= utcnow():
        row.status = "revoked"
        session.add(row)
        session.commit()
        raise ForbiddenError("邀请已过期")
    if row.email and user.email and row.email != user.email.lower():
        raise ForbiddenError("邀请邮箱与当前账户不一致")
    org = session.get(Organization, row.org_id)
    if org is None:
        raise NotFoundError("组织不存在")
    membership = ensure_membership(session, org, user, row.role_key)
    row.status = "accepted"
    row.accepted_at = utcnow()
    session.add(row)
    session.commit()
    audit.record(
        session, "member.joined", actor_id=user.id, actor_name=user.name, org_id=org.id,
        detail={"email": row.email, "role_key": row.role_key}, commit=True,
    )
    return membership


def serialize_user(user: User, membership: Membership | None = None) -> dict[str, Any]:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "avatar": user.avatar,
        "status": user.status,
        "is_platform_admin": user.is_platform_admin,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
        "created_at": user.created_at.isoformat(),
        "role_key": membership.role_key if membership else "",
        "membership_id": membership.id if membership else "",
        "membership_status": membership.status if membership else "",
        "has_password": bool(user.password_hash),
    }


def serialize_org(
    org: Organization, *, role_key: str = "", counts: dict[str, int] | None = None
) -> dict[str, Any]:
    return {
        "id": org.id,
        "slug": org.slug,
        "name": org.name,
        "description": org.description,
        "plan": org.plan,
        "status": org.status,
        "owner_id": org.owner_id,
        "settings": org.settings,
        "role_key": role_key,
        "counts": counts or {},
        "created_at": org.created_at.isoformat(),
    }


def serialize_provider(row: IdentityProvider, *, include_schema: bool = False) -> dict[str, Any]:
    schema = auth_providers.SCHEMA.get(row.kind, {})
    provider = auth_providers.build(row)
    payload: dict[str, Any] = {
        "id": row.id,
        "org_id": row.org_id,
        "key": row.key,
        "kind": row.kind,
        "name": row.name,
        "icon": row.icon or row.kind,
        "enabled": row.enabled,
        "config": row.config,
        "has_secret": any(bool(value) for value in (row.secret or {}).values()),
        "default_role_key": row.default_role_key,
        "auto_create_users": row.auto_create_users,
        "auto_join": row.auto_join,
        "allowed_domains": row.allowed_domains,
        "status": row.status,
        "missing": provider.missing(),
        "last_error": row.last_error,
        "last_check_at": row.last_check_at.isoformat() if row.last_check_at else None,
        "schema": schema if include_schema else None,
    }
    return payload
