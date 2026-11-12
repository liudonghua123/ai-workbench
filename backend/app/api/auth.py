"""Authentication endpoints.

Two families live here:

* **Public** — listing providers, starting a round trip, the callback, local
  login/signup, and the demo IdP. These must work before the caller holds a token.
* **Authenticated** — ``/auth/me``, organization switching, session management.

The callback ends with a ``302`` to the SPA with the session token in the URL
**fragment** (``#token=…``). A fragment is never sent to a server, so it stays out
of access logs and ``Referer`` headers; the SPA reads it once and clears it.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session, select

from app.core import passwords, security
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.security import Principal, get_principal
from app.models import (
    Identity,
    IdentityProvider,
    Membership,
    MemberStatus,
    Organization,
    Session as SessionModel,
    User,
)
from app.providers import auth as auth_providers
from app.schemas import LocalLoginRequest, PasswordChange, SignupRequest, SwitchOrgRequest
from app.services import audit, identity

logger = logging.getLogger("workbench.auth.api")

router = APIRouter(prefix="/auth", tags=["auth"])


def _callback_uri(request: Request, provider_key: str) -> str:
    base = settings.public_api_base.strip().rstrip("/")
    if not base:
        host = request.headers.get("host") or "127.0.0.1:8000"
        base = f"{request.url.scheme}://{host}"
    return f"{base}{settings.api_prefix}/auth/{provider_key}/callback"


def _client(request: Request) -> tuple[str, str]:
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = (forwarded.split(",")[0].strip() if forwarded else "") or (
        request.client.host if request.client else ""
    )
    return ip, request.headers.get("user-agent", "")


def _require_identity(principal: Principal) -> Principal:
    if principal.anonymous or not principal.user_id:
        raise UnauthorizedError("需要登录", details={"scheme": "Bearer"})
    return principal


# --------------------------------------------------------------------------
# discovery (public)
# --------------------------------------------------------------------------


@router.get("/providers")
def list_providers(session: Session = Depends(get_session)):
    """What the login page should render. Safe to call without a token."""
    rows = session.exec(
        select(IdentityProvider).where(IdentityProvider.enabled == True)  # noqa: E712
    ).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.kind == "local":
            continue
        provider = auth_providers.build(row)
        org = session.get(Organization, row.org_id) if row.org_id else None
        items.append(
            {
                "key": row.key,
                "kind": row.kind,
                "name": row.name,
                "icon": row.icon or row.kind,
                "mode": provider.mode,
                "org_id": row.org_id,
                # two organizations can each run their own connection to the same
                # IdP; the label is what tells them apart on the login page
                "org_name": org.name if org else "",
                "org_slug": org.slug if org else "",
                "ready": provider.ready(),
                "missing": provider.missing(),
            }
        )
    items.sort(key=lambda item: (item["kind"] == "mock", item["name"]))
    return {
        "auth_enabled": settings.auth_enabled,
        "local_login": True,
        "signup_enabled": settings.auth_allow_signup,
        "providers": items,
        "count": len(items),
    }


@router.get("/{provider_key}/start")
def start(
    provider_key: str,
    request: Request,
    return_to: str = Query(default="/"),
    session: Session = Depends(get_session),
):
    """Return the URL to send the browser to (the SPA navigates itself)."""
    provider = identity.provider_by_key(session, provider_key)
    redirect_uri = _callback_uri(request, provider.key)
    row, url = identity.start_sso(
        session, provider, redirect_uri=redirect_uri, return_to=return_to
    )
    return {"authorize_url": url, "state": row.state, "redirect_uri": redirect_uri}


@router.get("/{provider_key}/callback")
def callback(
    provider_key: str,
    request: Request,
    code: str = Query(default=""),
    state: str = Query(default=""),
    ticket: str = Query(default=""),
    error: str = Query(default=""),
    error_description: str = Query(default=""),
    session: Session = Depends(get_session),
):
    """Handle the IdP redirect: verify, provision, mint a session, hand off to the SPA."""
    frontend = settings.frontend_url.rstrip("/")
    if error:
        return RedirectResponse(
            f"{frontend}/login#error={_q(error_description or error)}", status_code=302
        )
    ip, user_agent = _client(request)
    try:
        token, user, org, return_to = identity.complete_sso(
            session, state=state, code=code or ticket, ip=ip, user_agent=user_agent
        )
    except (UnauthorizedError, ForbiddenError, NotFoundError) as exc:
        logger.info("sso callback rejected: %s", exc.message)
        return RedirectResponse(
            f"{frontend}/login#error={_q(exc.message)}", status_code=302
        )
    target = f"{frontend}/auth/callback#token={_q(token)}&next={_q(return_to or '/')}"
    return RedirectResponse(target, status_code=302)


def _q(value: str) -> str:
    from urllib.parse import quote

    return quote(value or "", safe="")


# --------------------------------------------------------------------------
# local account (public)
# --------------------------------------------------------------------------


@router.post("/local/login")
def local_login(
    payload: LocalLoginRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    ip, user_agent = _client(request)
    token, user, org = identity.login_with_password(
        session, payload.email, payload.password, ip=ip, user_agent=user_agent
    )
    return {
        "token": token,
        "user": identity.serialize_user(user),
        "org": identity.serialize_org(org),
        "expires_in": settings.auth_session_hours * 3600,
    }


@router.post("/signup", status_code=201)
def signup(
    payload: SignupRequest,
    request: Request,
    session: Session = Depends(get_session),
):
    ip, user_agent = _client(request)
    token, user, org = identity.signup(
        session,
        email=payload.email,
        password=payload.password,
        name=payload.name,
        org_name=payload.org_name,
        ip=ip,
        user_agent=user_agent,
    )
    return {
        "token": token,
        "user": identity.serialize_user(user),
        "org": identity.serialize_org(org, role_key="owner" if payload.org_name else "developer"),
        "expires_in": settings.auth_session_hours * 3600,
    }


# --------------------------------------------------------------------------
# authenticated
# --------------------------------------------------------------------------


@router.get("/me")
def me(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    orgs: list[dict[str, Any]] = []
    if settings.auth_enabled and principal.anonymous:
        # /auth/* is outside the middleware gate, so ask explicitly
        raise UnauthorizedError("需要登录", details={"scheme": "Bearer"})
    if principal.anonymous:
        # auth disabled: everybody is the anonymous owner of the default org, and
        # the UI still needs a real organization to render the access pages
        org = identity.default_org(session)
        orgs.append(identity.serialize_org(org, role_key="owner"))
    else:
        for ref in principal.orgs:
            org = session.get(Organization, ref.id)
            if org is None:
                continue
            members = session.exec(
                select(Membership).where(
                    Membership.org_id == org.id, Membership.status == MemberStatus.ACTIVE
                )
            ).all()
            orgs.append(
                identity.serialize_org(org, role_key=ref.role_key, counts={"members": len(members)})
            )

    user = session.get(User, principal.user_id) if principal.user_id else None
    active = next((o for o in orgs if o["id"] == principal.org_id), orgs[0] if orgs else None)
    return {
        "principal": principal.to_dict(),
        "user": identity.serialize_user(user) if user else None,
        "auth_enabled": settings.auth_enabled,
        "organizations": orgs,
        "active_org": active,
        "sessions": [
            {
                "id": row.id,
                "ip": row.ip,
                "user_agent": row.user_agent,
                "created_at": row.created_at.isoformat(),
                "expires_at": row.expires_at.isoformat(),
                "current": row.id == principal.session_id,
            }
            for row in identity.list_sessions(session, principal.user_id)
        ]
        if principal.user_id
        else [],
    }


@router.post("/logout")
def logout(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    if principal.session_id:
        identity.revoke_session(session, principal.session_id)
        audit.record(
            session, "auth.logout", actor_id=principal.user_id, actor_name=principal.name,
            org_id=principal.org_id, commit=True,
        )
    return {"ok": True}


@router.post("/switch-org")
def switch_org(
    payload: SwitchOrgRequest,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    """Pin the session to another organization the user belongs to."""
    _require_identity(principal)
    target = next(
        (ref for ref in principal.orgs if payload.org_id in (ref.id, ref.slug)), None
    )
    if target is None:
        raise ForbiddenError("你不属于该组织")
    row = session.get(SessionModel, principal.session_id)
    if row is None:
        raise UnauthorizedError("会话不存在")
    row.org_id = target.id
    session.add(row)
    session.commit()
    return {"ok": True, "org_id": target.id, "slug": target.slug, "role_key": target.role_key}


@router.delete("/sessions/{session_id}", status_code=204)
def revoke_session(
    session_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    _require_identity(principal)
    row = session.get(SessionModel, session_id)
    if row is None:
        raise NotFoundError("会话不存在")
    if row.user_id != principal.user_id and not principal.is_platform_admin:
        raise ForbiddenError("不能吊销他人的会话")
    identity.revoke_session(session, session_id)
    audit.record(
        session, "auth.session_revoked", actor_id=principal.user_id,
        actor_name=principal.name, org_id=principal.org_id,
        resource_type="session", resource_id=session_id, commit=True,
    )


@router.post("/password")
def change_password(
    payload: PasswordChange,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    _require_identity(principal)
    user = session.get(User, principal.user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    if user.password_hash and not passwords.verify_password(
        payload.current_password, user.password_hash
    ):
        raise ForbiddenError("当前密码不正确")
    if len(payload.new_password) < 8:
        raise ForbiddenError("新密码至少 8 位")
    identity.set_password(session, user, payload.new_password)
    identity.revoke_sessions_for(session, user.id)
    return {"ok": True, "note": "其他会话已失效"}


# --------------------------------------------------------------------------
# demo identity provider — a real OIDC-shaped flow, served by us
# --------------------------------------------------------------------------


def _mock_code(email: str, name: str, groups: str) -> str:
    payload = {
        "sub": f"mock|{email.lower()}",
        "email": email.lower(),
        "name": name or email.split("@")[0],
        "groups": [item.strip() for item in (groups or "").split(",") if item.strip()],
    }
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")


def _mock_decode(code: str) -> dict[str, Any]:
    padded = code + "=" * (-len(code) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except Exception as exc:  # noqa: BLE001
        raise UnauthorizedError("演示身份源的授权码无效") from exc


@router.get("/mock/authorize", response_class=HTMLResponse)
def mock_authorize(
    redirect_uri: str = Query(default=""),
    state: str = Query(default=""),
    email: str = Query(default=""),
    name: str = Query(default=""),
    groups: str = Query(default=""),
    auto: int = Query(default=0),
):
    """Consent screen for the built-in demo IdP.

    With ``?auto=1&email=…`` it redirects immediately — that is how the automated
    walk-through exercises the whole SSO path without clicking.
    """
    if not redirect_uri:
        raise ForbiddenError("缺少 redirect_uri")
    chosen_email = email or ("demo@workbench.local" if auto else "")
    if auto and chosen_email:
        code = _mock_code(chosen_email, name or "Demo User", groups)
        return RedirectResponse(
            f"{redirect_uri}{'&' if '?' in redirect_uri else '?'}code={_q(code)}&state={_q(state)}",
            status_code=302,
        )

    from html import escape

    body = f"""<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Demo IdP</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ margin:0; min-height:100vh; display:grid; place-items:center;
         font:14px/1.6 ui-sans-serif,system-ui,"Segoe UI",sans-serif;
         background:#f6f7f9; color:#12141a; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#0d0f13; color:#e8eaf0; }} 
    .card {{ background:#15181e !important; border-color:#252a33 !important; }} 
    input {{ background:#0d0f13 !important; color:#e8eaf0 !important; border-color:#2c313b !important; }} }}
  .card {{ width:min(380px,92vw); background:#fff; border:1px solid #e3e6ec;
          border-radius:14px; padding:24px; box-shadow:0 12px 32px rgba(12,16,24,.08); }}
  h1 {{ font-size:15px; margin:0 0 4px; }}
  p.sub {{ margin:0 0 18px; font-size:12px; color:#6b7280; }}
  label {{ display:block; font-size:12px; margin:12px 0 4px; color:#6b7280; }}
  input {{ width:100%; box-sizing:border-box; padding:8px 10px; border-radius:8px;
          border:1px solid #d7dbe3; font:inherit; }}
  button {{ margin-top:18px; width:100%; padding:9px; border:0; border-radius:8px;
           background:#2f6df6; color:#fff; font:inherit; font-weight:600; cursor:pointer; }}
  code {{ font-size:11px; color:#6b7280; word-break:break-all; }}
</style></head>
<body><form class="card" method="get" action="{settings.api_prefix}/auth/mock/authorize">
  <h1>Demo Identity Provider</h1>
  <p class="sub">由工作台自身提供，仅用于在本地验证完整登录链路，不做任何签名校验。</p>
  <input type="hidden" name="redirect_uri" value="{escape(redirect_uri)}">
  <input type="hidden" name="state" value="{escape(state)}">
  <label for="email">邮箱</label>
  <input id="email" name="email" value="{escape(email or 'demo@workbench.local')}" required>
  <label for="name">显示名</label>
  <input id="name" name="name" value="{escape(name or 'Demo User')}">
  <label for="groups">用户组（逗号分隔，用于映射角色）</label>
  <input id="groups" name="groups" value="{escape(groups)}" placeholder="wb-developers">
  <button type="submit">继续并授权</button>
  <p class="sub" style="margin:12px 0 0">回调地址 <code>{escape(redirect_uri)}</code></p>
</form></body></html>"""
    return HTMLResponse(body)


@router.post("/mock/token")
def mock_token(payload: dict[str, Any]):
    code = str(payload.get("code") or "")
    if not code:
        raise UnauthorizedError("缺少 code")
    profile = _mock_decode(code)
    return {**profile, "iss": "workbench-demo", "aud": "workbench"}

