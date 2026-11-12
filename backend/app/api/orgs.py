"""Organizations, members, roles, project bindings, SSO connections, audit.

Authorisation happens twice on purpose, and both layers are needed:

* the **middleware** answers "does this caller hold ``member:manage`` in the
  organization their token is pinned to";
* the **handlers here** answer "…and are they a member of the organization in the
  URL". Without the second check, being an admin of org A would let you edit
  org B by changing a path segment.

Helpers live at the top: ``_require`` is the single place that decides what "may
act in this org" means.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.core import permissions as perms
from app.core import security
from app.core.config import settings
from app.core.db import get_session
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.ids import new_id, slugify
from app.core.security import Principal, get_principal
from app.models import (
    AuditLog,
    IdentityProvider,
    Invitation,
    Membership,
    MemberStatus,
    Organization,
    Project,
    Role,
    RoleBinding,
    User,
)
from app.providers import auth as auth_providers
from app.schemas import (
    BindingCreate,
    InvitationAccept,
    InvitationCreate,
    MemberAdd,
    MemberUpdate,
    OrgCreate,
    OrgUpdate,
    ProviderUpsert,
    RoleUpsert,
)
from app.services import audit, identity
from app.services.identity import ensure_builtin_roles

router = APIRouter(tags=["access"])


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _org(session: Session, org_id: str) -> Organization:
    org = session.get(Organization, org_id)
    if org is None:
        raise NotFoundError(f"组织不存在: {org_id}")
    return org


def _membership(session: Session, org_id: str, user_id: str) -> Membership | None:
    return identity.membership_for(session, org_id, user_id)


def _permissions_in(session: Session, org: Organization, membership: Membership) -> frozenset[str]:
    return security.effective_permissions(session, org.id, membership.role_key)


def _require(
    session: Session, principal: Principal, org_id: str, permission: str
) -> tuple[Organization, Membership | None]:
    """Membership + permission check against a *specific* organization.

    ``WB_AUTH_TOKENS`` principals are the deployment operator's credentials: they
    hold no membership row by design, but they are the documented way to bootstrap
    an organization whose only login path is SSO (otherwise nobody could name the
    first owner). They are still limited to the permissions their token carries.
    """
    org = _org(session, org_id)
    if principal.anonymous or principal.is_platform_admin:
        return org, None
    if principal.auth_method == "bootstrap":
        if permission and not principal.has(permission):
            raise ForbiddenError(f"令牌缺少权限 {permission}")
        return org, None
    membership = _membership(session, org.id, principal.user_id)
    if membership is None or membership.status != MemberStatus.ACTIVE:
        # do not confirm the org exists to outsiders
        raise NotFoundError(f"组织不存在: {org_id}")
    if not permission:
        return org, membership
    granted = _permissions_in(session, org, membership)
    if permission not in granted and "*" not in granted:
        raise ForbiddenError(f"角色 {membership.role_key} 缺少权限 {permission}")
    return org, membership


def _localized(role_key: str, fallback: str, field: str) -> str:
    """Builtin roles are declared once with an English and a Chinese label."""
    builtin = perms.BUILTIN_BY_KEY.get(role_key)
    if builtin is None:
        return fallback
    value = getattr(builtin, field, "")
    return value or fallback


def _role_for_org(session: Session, org: Organization, key: str) -> Role:
    row = session.exec(select(Role).where(Role.org_id == org.id, Role.key == key)).first()
    if row is None:
        raise ForbiddenError(f"组织中不存在角色: {key}")
    return row


# --------------------------------------------------------------------------
# organizations
# --------------------------------------------------------------------------


@router.get("/orgs")
def list_orgs(
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    if principal.anonymous:
        org = identity.default_org(session)
        return [identity.serialize_org(org, role_key="owner")]
    if principal.is_platform_admin:
        rows = session.exec(select(Organization).order_by(Organization.name)).all()  # type: ignore[attr-defined]
    else:
        rows = [
            session.get(Organization, ref.id)
            for ref in principal.orgs
        ]
    items: list[dict[str, Any]] = []
    for org in [row for row in rows if row is not None]:
        membership = _membership(session, org.id, principal.user_id) if principal.user_id else None
        members = session.exec(
            select(Membership).where(
                Membership.org_id == org.id, Membership.status == MemberStatus.ACTIVE
            )
        ).all()
        projects = session.exec(select(Project).where(Project.org_id == org.id)).all()
        items.append(
            identity.serialize_org(
                org,
                role_key=membership.role_key if membership else "owner",
                counts={"members": len(members), "projects": len(projects)},
            )
        )
    return items


@router.post("/orgs", status_code=201)
def create_org(
    payload: OrgCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    user = session.get(User, principal.user_id) if principal.user_id else None
    org = identity.create_organization(
        session,
        name=payload.name,
        slug=payload.slug,
        owner=user,
        description=payload.description,
        plan=payload.plan,
    )
    return identity.serialize_org(org, role_key="owner", counts={"members": 1, "projects": 0})


@router.get("/orgs/{org_id}")
def get_org(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, membership = _require(session, principal, org_id, "org:read")
    return identity.serialize_org(org, role_key=membership.role_key if membership else "owner")


@router.patch("/orgs/{org_id}")
def update_org(
    org_id: str,
    payload: OrgUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, membership = _require(session, principal, org_id, "org:update")
    for field in ("name", "description", "plan", "status"):
        value = getattr(payload, field)
        if value is not None:
            setattr(org, field, value)
    if payload.settings is not None:
        org.settings = {**(org.settings or {}), **payload.settings}
    org.updated_at = identity.utcnow()
    session.add(org)
    session.commit()
    session.refresh(org)
    audit.record(
        session, "org.updated", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="organization", resource_id=org.id,
        detail={"fields": payload.model_dump(exclude_none=True)}, commit=True,
    )
    return identity.serialize_org(org, role_key=membership.role_key if membership else "owner")


@router.delete("/orgs/{org_id}", status_code=204)
def delete_org(
    org_id: str,
    purge_projects: bool = Query(default=False),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _membership_row = _require(session, principal, org_id, "org:delete")
    projects = session.exec(select(Project).where(Project.org_id == org.id)).all()
    if projects and not purge_projects:
        raise ConflictError(
            f"组织下还有 {len(projects)} 个项目，确认删除请带 purge_projects=true"
        )
    if projects:
        from app.services import scaffold

        for project in projects:
            try:
                scaffold.delete_project(session, project, purge_files=True)
            except Exception:  # noqa: BLE001 - keep deleting the rest
                session.rollback()
    for model in (Membership, Role, RoleBinding, IdentityProvider, Invitation):
        for row in session.exec(select(model).where(model.org_id == org.id)).all():
            session.delete(row)
    session.delete(org)
    session.commit()
    audit.record(
        session, "org.deleted", actor_id=principal.user_id, actor_name=principal.name,
        org_id="", resource_type="organization", resource_id=org_id,
        detail={"slug": org.slug, "projects": len(projects)}, commit=True,
    )


# --------------------------------------------------------------------------
# members
# --------------------------------------------------------------------------


@router.get("/orgs/{org_id}/members")
def list_members(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:read")
    memberships = session.exec(select(Membership).where(Membership.org_id == org.id)).all()
    items: list[dict[str, Any]] = []
    for membership in memberships:
        user = session.get(User, membership.user_id)
        if user is None:
            continue
        payload = identity.serialize_user(user, membership)
        payload["title"] = membership.title
        payload["bindings"] = [
            {"id": b.id, "scope_type": b.scope_type, "scope_id": b.scope_id,
             "role_key": b.role_key, "permissions": b.permissions}
            for b in session.exec(
                select(RoleBinding).where(
                    RoleBinding.org_id == org.id, RoleBinding.user_id == user.id
                )
            ).all()
        ]
        items.append(payload)
    items.sort(key=lambda item: (item["role_key"] != "owner", item["name"] or item["email"]))
    return items


@router.post("/orgs/{org_id}/members", status_code=201)
def add_member(
    org_id: str,
    payload: MemberAdd,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    _role_for_org(session, org, payload.role_key)

    user: User | None = None
    if payload.user_id:
        user = session.get(User, payload.user_id)
    elif payload.email:
        user = identity.user_by_email(session, payload.email)

    if user is None:
        # invite by email instead of inventing a password
        invitation, token = identity.invite(
            session, org, email=payload.email, role_key=payload.role_key,
            invited_by=principal.name,
        )
        return {
            "invited": True,
            "invitation": _serialize_invitation(invitation),
            "accept_token": token if settings.debug else "",
            "note": "该邮箱还没有账户，已生成邀请，接受后自动加入组织",
        }

    membership = identity.membership_for(session, org.id, user.id)
    if membership is not None and membership.status == MemberStatus.ACTIVE:
        raise ConflictError(f"{user.email} 已经是组织成员")
    if membership is None:
        membership = identity.ensure_membership(session, org, user, payload.role_key)
    else:
        membership.role_key = payload.role_key
        membership.status = MemberStatus.ACTIVE
        session.add(membership)
        session.commit()
    if payload.title:
        membership.title = payload.title
        session.add(membership)
        session.commit()
    audit.record(
        session, "member.joined", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="user", resource_id=user.id,
        detail={"email": user.email, "role_key": membership.role_key}, commit=True,
    )
    return identity.serialize_user(user, membership)


@router.patch("/orgs/{org_id}/members/{membership_id}")
def update_member(
    org_id: str,
    membership_id: str,
    payload: MemberUpdate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    membership = session.get(Membership, membership_id)
    if membership is None or membership.org_id != org.id:
        raise NotFoundError("成员不存在")
    user = session.get(User, membership.user_id)
    if membership.role_key == "owner" and payload.role_key and payload.role_key != "owner":
        raise ForbiddenError("请先转让所有权，不能直接降级所有者")

    if payload.role_key:
        _role_for_org(session, org, payload.role_key)
        membership.role_key = payload.role_key
    if payload.status:
        membership.status = payload.status
    if payload.title is not None:
        membership.title = payload.title
    membership.updated_at = identity.utcnow()
    session.add(membership)
    session.commit()
    session.refresh(membership)
    audit.record(
        session, "member.role_changed", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="user", resource_id=membership.user_id,
        detail=payload.model_dump(exclude_none=True), commit=True,
    )
    return identity.serialize_user(user, membership) if user else {"id": membership.id}


@router.delete("/orgs/{org_id}/members/{membership_id}", status_code=204)
def remove_member(
    org_id: str,
    membership_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    membership = session.get(Membership, membership_id)
    if membership is None or membership.org_id != org.id:
        raise NotFoundError("成员不存在")
    if membership.role_key == "owner":
        raise ForbiddenError("不能移除组织所有者")
    user_id = membership.user_id
    for binding in session.exec(
        select(RoleBinding).where(
            RoleBinding.org_id == org.id, RoleBinding.user_id == user_id
        )
    ).all():
        session.delete(binding)
    session.delete(membership)
    session.commit()
    audit.record(
        session, "member.removed", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="user", resource_id=user_id, commit=True,
    )


@router.post("/orgs/{org_id}/members/{membership_id}/reset-password")
def reset_member_password(
    org_id: str,
    membership_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    """Admin sets a temporary password; every existing session is revoked."""
    org, _row = _require(session, principal, org_id, "member:manage")
    membership = session.get(Membership, membership_id)
    if membership is None or membership.org_id != org.id:
        raise NotFoundError("成员不存在")
    user = session.get(User, membership.user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    import secrets

    temporary = secrets.token_urlsafe(9)
    identity.set_password(session, user, temporary)
    identity.revoke_sessions_for(session, user.id)
    return {"ok": True, "temporary_password": temporary}


# --------------------------------------------------------------------------
# invitations
# --------------------------------------------------------------------------


def _serialize_invitation(row: Invitation) -> dict[str, Any]:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "email": row.email,
        "role_key": row.role_key,
        "status": row.status,
        "invited_by": row.invited_by,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "created_at": row.created_at.isoformat(),
    }


@router.get("/orgs/{org_id}/invitations")
def list_invitations(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:read")
    rows = session.exec(
        select(Invitation)
        .where(Invitation.org_id == org.id)
        .order_by(Invitation.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [_serialize_invitation(row) for row in rows]


@router.post("/orgs/{org_id}/invitations", status_code=201)
def create_invitation(
    org_id: str,
    payload: InvitationCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    _role_for_org(session, org, payload.role_key)
    invitation, token = identity.invite(
        session, org, email=payload.email, role_key=payload.role_key,
        invited_by=principal.name, ttl_days=payload.ttl_days,
    )
    return {
        **_serialize_invitation(invitation),
        "accept_token": token,
        "accept_url": f"{settings.frontend_url.rstrip('/')}/invite#token={token}",
    }


@router.delete("/orgs/{org_id}/invitations/{invitation_id}", status_code=204)
def revoke_invitation(
    org_id: str,
    invitation_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    row = session.get(Invitation, invitation_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("邀请不存在")
    row.status = "revoked"
    session.add(row)
    session.commit()


@router.post("/invitations/accept")
def accept_invitation(
    payload: InvitationAccept,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    if principal.anonymous or not principal.user_id:
        raise ForbiddenError("请先登录再接受邀请")
    user = session.get(User, principal.user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    membership = identity.accept_invitation(session, payload.token, user)
    org = session.get(Organization, membership.org_id)
    return {
        "ok": True,
        "org": identity.serialize_org(org, role_key=membership.role_key) if org else None,
        "role_key": membership.role_key,
    }


# --------------------------------------------------------------------------
# roles & permission catalog
# --------------------------------------------------------------------------


@router.get("/permissions")
def permission_catalog():
    return perms.catalog()


@router.get("/orgs/{org_id}/roles")
def list_roles(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "role:read")
    ensure_builtin_roles(session, org)
    rows = session.exec(select(Role).where(Role.org_id == org.id)).all()
    counts = {}
    for membership in session.exec(select(Membership).where(Membership.org_id == org.id)).all():
        counts[membership.role_key] = counts.get(membership.role_key, 0) + 1
    items = [
        {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            # builtins carry a translated label; custom roles keep what was typed
            "name_zh": _localized(row.key, row.name, "name_zh"),
            "description": row.description,
            "description_zh": _localized(row.key, row.description, "description_zh"),
            "permissions": row.permissions,
            "expanded": sorted(perms.expand(row.permissions)),
            "count": len(perms.expand(row.permissions)),
            "builtin": row.builtin,
            "rank": row.rank,
            "members": counts.get(row.key, 0),
        }
        for row in rows
    ]
    items.sort(key=lambda item: -item["rank"])
    return items


@router.post("/orgs/{org_id}/roles", status_code=201)
def create_role(
    org_id: str,
    payload: RoleUpsert,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "role:manage")
    key = slugify(payload.key or payload.name) or f"role-{new_id('x')[2:8]}"
    if session.exec(select(Role).where(Role.org_id == org.id, Role.key == key)).first():
        raise ConflictError(f"角色标识已存在: {key}")
    try:
        grants = perms.normalize(payload.permissions)
    except ValueError as exc:
        raise ForbiddenError(str(exc)) from exc
    if not grants:
        raise ForbiddenError("至少要选择一个权限")
    row = Role(
        id=new_id("rol"),
        org_id=org.id,
        key=key,
        name=payload.name.strip() or key,
        description=payload.description,
        permissions=grants,
        builtin=False,
        rank=payload.rank,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "role.created", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="role", resource_id=row.id,
        detail={"key": key, "permissions": grants}, commit=True,
    )
    return {"id": row.id, "key": row.key, "name": row.name,
            "name_zh": _localized(row.key, row.name, "name_zh"),
            "description": row.description,
            "description_zh": _localized(row.key, row.description, "description_zh"),
            "permissions": row.permissions, "expanded": sorted(perms.expand(row.permissions)),
            "count": len(perms.expand(row.permissions)), "builtin": False, "rank": row.rank,
            "members": 0}


@router.patch("/orgs/{org_id}/roles/{role_id}")
def update_role(
    org_id: str,
    role_id: str,
    payload: RoleUpsert,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "role:manage")
    row = session.get(Role, role_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("角色不存在")
    if row.key == "owner":
        raise ForbiddenError("所有者角色的权限不可修改")
    if payload.permissions:
        try:
            row.permissions = perms.normalize(payload.permissions)
        except ValueError as exc:
            raise ForbiddenError(str(exc)) from exc
    if payload.name:
        row.name = payload.name
    if payload.description is not None:
        row.description = payload.description
    row.rank = payload.rank
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "role.updated", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="role", resource_id=row.id,
        detail={"permissions": row.permissions}, commit=True,
    )
    return {"id": row.id, "key": row.key, "name": row.name,
            "name_zh": _localized(row.key, row.name, "name_zh"),
            "description": row.description,
            "description_zh": _localized(row.key, row.description, "description_zh"),
            "permissions": row.permissions, "expanded": sorted(perms.expand(row.permissions)),
            "count": len(perms.expand(row.permissions)), "builtin": row.builtin,
            "rank": row.rank, "members": 0}


@router.delete("/orgs/{org_id}/roles/{role_id}", status_code=204)
def delete_role(
    org_id: str,
    role_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "role:manage")
    row = session.get(Role, role_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("角色不存在")
    if row.builtin:
        raise ForbiddenError("内置角色不能删除")
    in_use = session.exec(
        select(Membership).where(Membership.org_id == org.id, Membership.role_key == row.key)
    ).all()
    if in_use:
        raise ConflictError(f"还有 {len(in_use)} 名成员使用该角色")
    session.delete(row)
    session.commit()
    audit.record(
        session, "role.deleted", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="role", resource_id=role_id, commit=True,
    )


# --------------------------------------------------------------------------
# project-scoped bindings
# --------------------------------------------------------------------------


@router.get("/orgs/{org_id}/bindings")
def list_bindings(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:read")
    rows = session.exec(select(RoleBinding).where(RoleBinding.org_id == org.id)).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "role_key": row.role_key,
            "permissions": row.permissions,
            "scope_type": row.scope_type,
            "scope_id": row.scope_id,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.post("/orgs/{org_id}/bindings", status_code=201)
def create_binding(
    org_id: str,
    payload: BindingCreate,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    user = session.get(User, payload.user_id)
    if user is None:
        raise NotFoundError("用户不存在")
    if identity.membership_for(session, org.id, user.id) is None:
        raise ForbiddenError("请先把用户加入组织，再授予项目级权限")
    if payload.scope_type == "project":
        project = session.get(Project, payload.scope_id)
        if project is None or project.org_id != org.id:
            raise NotFoundError("项目不属于该组织")
    try:
        grants = perms.normalize(payload.permissions)
    except ValueError as exc:
        raise ForbiddenError(str(exc)) from exc
    if payload.role_key:
        _role_for_org(session, org, payload.role_key)
    if not grants and not payload.role_key:
        raise ForbiddenError("请指定角色或权限")
    row = RoleBinding(
        id=new_id("bnd"),
        org_id=org.id,
        user_id=user.id,
        role_key=payload.role_key,
        permissions=grants,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        created_by=principal.name,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "binding.created", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type=payload.scope_type, resource_id=payload.scope_id,
        detail={"user": user.email, "role_key": payload.role_key, "permissions": grants},
        commit=True,
    )
    return {"id": row.id, "user_id": row.user_id, "role_key": row.role_key,
            "permissions": row.permissions, "scope_type": row.scope_type,
            "scope_id": row.scope_id, "created_at": row.created_at.isoformat()}


@router.delete("/orgs/{org_id}/bindings/{binding_id}", status_code=204)
def delete_binding(
    org_id: str,
    binding_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "member:manage")
    row = session.get(RoleBinding, binding_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("授权不存在")
    session.delete(row)
    session.commit()
    audit.record(
        session, "binding.removed", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type=row.scope_type, resource_id=row.scope_id, commit=True,
    )


# --------------------------------------------------------------------------
# SSO connections
# --------------------------------------------------------------------------


@router.get("/orgs/{org_id}/sso/schema")
def sso_schema(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    """Field metadata for the connection form — one source for UI and validation."""
    _require(session, principal, org_id, "sso:read")
    return {
        "kinds": [
            {"kind": kind, **schema} for kind, schema in auth_providers.SCHEMA.items()
        ],
        "common": [
            {"key": "name", "label": "Display name", "label_zh": "显示名称", "required": True},
            {"key": "default_role_key", "label": "Default role", "label_zh": "默认角色",
             "type": "role", "default": "viewer"},
            {"key": "allowed_domains", "label": "Allowed email domains",
             "label_zh": "允许的邮箱域", "type": "list",
             "help": "留空表示不限制", "help_en": "Empty means no restriction"},
            {"key": "auto_create_users", "label": "Auto-create accounts",
             "label_zh": "自动开户", "type": "switch", "default": True},
            {"key": "auto_join", "label": "Auto-join organization",
             "label_zh": "自动加入组织", "type": "switch", "default": True},
            # every provider field lives in config; role_map is the only common
            # one that is JSON rather than a scalar
            {"key": "role_map", "label": "Group to role mapping",
             "label_zh": "用户组 → 角色映射", "type": "json",
             "placeholder": '{"wb-admins": "admin"}',
             "help": "IdP 返回的用户组按这张表映射到角色，命中多个时取 rank 最高者",
             "help_en": "Maps IdP groups onto roles; the highest-ranked match wins"},
        ],
    }


@router.get("/orgs/{org_id}/sso")
def list_sso(
    org_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "sso:read")
    rows = session.exec(select(IdentityProvider).where(IdentityProvider.org_id == org.id)).all()
    return [identity.serialize_provider(row) for row in rows]


@router.post("/orgs/{org_id}/sso", status_code=201)
def create_sso(
    org_id: str,
    payload: ProviderUpsert,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "sso:manage")
    kind = payload.kind if payload.kind in auth_providers.SCHEMA else "oidc"
    key = slugify(payload.key or payload.name or kind) or f"{kind}-{new_id('x')[2:8]}"
    if session.exec(select(IdentityProvider).where(IdentityProvider.key == key)).first():
        if payload.key:
            raise ConflictError(f"身份源标识已存在: {key}")
        # the key is the public login path segment, so it must be unique across the
        # whole install, not just this org; derive a suffix instead of failing
        key = f"{key}-{new_id('x')[2:8]}"
    _role_for_org(session, org, payload.default_role_key)
    config = dict(payload.config or {})
    if kind == "mock" and not config.get("base_url"):
        config["base_url"] = settings.public_api_base or "http://127.0.0.1:8000"
    row = IdentityProvider(
        id=new_id("idp"),
        org_id=org.id,
        key=key,
        kind=kind,
        name=payload.name or auth_providers.SCHEMA[kind]["label"],
        icon=payload.icon or kind,
        enabled=payload.enabled,
        config=config,
        secret=dict(payload.secret or {}),
        default_role_key=payload.default_role_key,
        auto_create_users=payload.auto_create_users,
        auto_join=payload.auto_join,
        allowed_domains=[d.strip().lower().lstrip("@") for d in payload.allowed_domains if d.strip()],
    )
    # configured != verified: only probe() is allowed to claim "ready"
    row.status = "unchecked" if auth_providers.build(row).ready() else "unconfigured"
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "sso.created", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="identity_provider", resource_id=row.id,
        detail={"kind": kind, "key": key}, commit=True,
    )
    return identity.serialize_provider(row, include_schema=True)


@router.patch("/orgs/{org_id}/sso/{provider_id}")
def update_sso(
    org_id: str,
    provider_id: str,
    payload: ProviderUpsert,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "sso:manage")
    row = session.get(IdentityProvider, provider_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("身份源不存在")
    if payload.name:
        row.name = payload.name
    if payload.icon:
        row.icon = payload.icon
    if payload.config:
        row.config = {**(row.config or {}), **payload.config}
    # secrets merge: an empty string means "keep the stored value"
    merged_secret = dict(row.secret or {})
    for key, value in (payload.secret or {}).items():
        if str(value or "").strip():
            merged_secret[key] = value
    row.secret = merged_secret
    if payload.default_role_key:
        _role_for_org(session, org, payload.default_role_key)
        row.default_role_key = payload.default_role_key
    row.auto_create_users = payload.auto_create_users
    row.auto_join = payload.auto_join
    row.allowed_domains = [d.strip().lower().lstrip("@") for d in payload.allowed_domains if d.strip()]
    row.enabled = payload.enabled
    row.status = "unchecked" if auth_providers.build(row).ready() else "unconfigured"
    row.updated_at = identity.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    audit.record(
        session, "sso.updated", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="identity_provider", resource_id=row.id,
        detail={"enabled": row.enabled, "status": row.status}, commit=True,
    )
    return identity.serialize_provider(row, include_schema=True)


@router.delete("/orgs/{org_id}/sso/{provider_id}", status_code=204)
def delete_sso(
    org_id: str,
    provider_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "sso:manage")
    row = session.get(IdentityProvider, provider_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("身份源不存在")
    session.delete(row)
    session.commit()
    audit.record(
        session, "sso.deleted", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="identity_provider", resource_id=provider_id, commit=True,
    )


@router.post("/orgs/{org_id}/sso/{provider_id}/probe")
def probe_sso(
    org_id: str,
    provider_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "sso:manage")
    row = session.get(IdentityProvider, provider_id)
    if row is None or row.org_id != org.id:
        raise NotFoundError("身份源不存在")
    result = auth_providers.build(row).probe()
    row.status = "ready" if result.get("ok") else ("error" if result.get("status") == "error" else "unconfigured")
    row.last_error = "" if result.get("ok") else str(result.get("message") or "")
    row.last_check_at = identity.utcnow()
    session.add(row)
    session.commit()
    audit.record(
        session, "sso.probed", actor_id=principal.user_id, actor_name=principal.name,
        org_id=org.id, resource_type="identity_provider", resource_id=row.id,
        status="ok" if result.get("ok") else "error",
        detail={"status": row.status, "message": result.get("message")}, commit=True,
    )
    return {**result, "provider": identity.serialize_provider(row)}


# --------------------------------------------------------------------------
# audit & users
# --------------------------------------------------------------------------


@router.get("/orgs/{org_id}/audit")
def list_audit(
    org_id: str,
    limit: int = Query(default=100, le=500),
    action: str = "",
    status: str = "",
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    org, _row = _require(session, principal, org_id, "audit:read")
    stmt = select(AuditLog).where(AuditLog.org_id == org.id)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if status:
        stmt = stmt.where(AuditLog.status == status)
    rows = session.exec(stmt.order_by(AuditLog.created_at.desc()).limit(limit)).all()  # type: ignore[attr-defined]
    return [audit.serialize(row) for row in rows]


@router.get("/audit")
def list_platform_audit(
    limit: int = Query(default=100, le=500),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    """Cross-org view for platform operators."""
    if not (principal.anonymous or principal.is_platform_admin):
        raise ForbiddenError("需要平台管理员权限")
    rows = session.exec(
        select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return [audit.serialize(row) for row in rows]


@router.get("/users")
def search_users(
    q: str = Query(default=""),
    limit: int = Query(default=20, le=100),
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    needle = q.strip().lower()
    rows = session.exec(select(User).order_by(User.created_at.desc()).limit(200)).all()  # type: ignore[attr-defined]
    if needle:
        rows = [
            row for row in rows
            if needle in (row.email or "").lower() or needle in (row.name or "").lower()
        ]
    return [identity.serialize_user(row) for row in rows[:limit]]


@router.get("/users/{user_id}/identities")
def user_identities(
    user_id: str,
    principal: Principal = Depends(get_principal),
    session: Session = Depends(get_session),
):
    from app.models import Identity

    if principal.user_id != user_id and not (principal.anonymous or principal.is_platform_admin):
        raise ForbiddenError("只能查看自己的身份绑定")
    rows = session.exec(select(Identity).where(Identity.user_id == user_id)).all()
    return [
        {
            "id": row.id,
            "provider_key": row.provider_key,
            "kind": row.kind,
            "subject": row.subject,
            "email": row.email,
            "display_name": row.display_name,
            "last_login_at": row.last_login_at.isoformat() if row.last_login_at else None,
        }
        for row in rows
    ]
