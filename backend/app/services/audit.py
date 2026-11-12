"""Append-only audit trail.

Two rules keep this useful rather than decorative:

* **Denials are recorded too.** "Who tried and failed" is the question you ask
  during an incident, and the answer is not in the request log once it rotates.
* **Writes never break the caller.** A failed audit insert logs a warning and
  returns; losing a row must not fail a deployment.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlmodel import Session

from app.core import context
from app.core.ids import new_id
from app.models import AuditLog

logger = logging.getLogger("workbench.audit")

# actions worth keeping longer than the request log
ACTIONS = {
    "auth.login": "登录",
    "auth.login_failed": "登录失败",
    "auth.logout": "登出",
    "auth.sso_start": "发起 SSO",
    "auth.sso_failed": "SSO 失败",
    "auth.session_revoked": "吊销会话",
    "org.created": "创建组织",
    "org.updated": "修改组织",
    "org.deleted": "删除组织",
    "member.invited": "邀请成员",
    "member.joined": "成员加入",
    "member.role_changed": "变更成员角色",
    "member.removed": "移除成员",
    "role.created": "创建角色",
    "role.updated": "修改角色",
    "role.deleted": "删除角色",
    "binding.created": "授予项目权限",
    "binding.removed": "回收项目权限",
    "sso.created": "新增身份源",
    "sso.updated": "修改身份源",
    "sso.deleted": "删除身份源",
    "sso.probed": "探测身份源",
    "project.created": "创建项目",
    "project.deleted": "删除项目",
    "project.published": "发布项目",
    "access.denied": "权限被拒绝",
}


def record(
    session: Session,
    action: str,
    *,
    actor_id: str = "",
    actor_name: str = "",
    org_id: str = "",
    resource_type: str = "",
    resource_id: str = "",
    status: str = "ok",
    detail: dict[str, Any] | None = None,
    ip: str = "",
    commit: bool = False,
) -> AuditLog | None:
    try:
        row = AuditLog(
            id=new_id("aud"),
            org_id=org_id,
            actor_id=actor_id,
            actor_name=actor_name,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            status=status,
            # fall back to the request in flight so a handler never has to thread
            # the socket address through just to fill this column
            ip=ip or context.current_client_ip(),
            detail=detail or {},
        )
        session.add(row)
        if commit:
            session.commit()
        return row
    except Exception:  # noqa: BLE001 - auditing must never break the request
        logger.warning("audit write failed: %s", action, exc_info=True)
        return None


def serialize(row: AuditLog) -> dict[str, Any]:
    return {
        "id": row.id,
        "org_id": row.org_id,
        "actor_id": row.actor_id,
        "actor_name": row.actor_name,
        "action": row.action,
        "label": ACTIONS.get(row.action, row.action),
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "status": row.status,
        "ip": row.ip,
        "detail": row.detail,
        "created_at": row.created_at.isoformat(),
    }
