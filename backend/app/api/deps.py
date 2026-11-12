"""Route dependencies shared by the project routers.

The middleware already answered "may this caller use this endpoint at all"
(see ``ROUTE_RULES``). What it cannot answer is *which rows* that permission
applies to — that needs the project itself. Resolving the row and checking the
data scope in one place is what keeps a path like ``GET /projects/{id}/env``
from becoming a way to read a colleague's secrets: every endpoint loads its
project through :func:`load_scoped` instead of calling ``scaffold.get_project``
directly.
"""

from __future__ import annotations

from fastapi import Depends
from sqlmodel import Session, select

from app.core.db import get_session
from app.core.security import Principal, can_access, get_principal, require_project
from app.models import Project


def load_scoped(session: Session, project_id: str, principal: Principal) -> Project:
    """The project, or 404 — including when it exists but is not yours.

    Returning 404 rather than 403 is deliberate: a 403 would confirm that the id
    exists in someone else's scope, which is exactly what an ``own`` data scope
    is supposed to hide.
    """
    return require_project(session, principal, project_id)


def scoped_project(
    project_id: str,
    session: Session = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> Project:
    """Dependency form of :func:`load_scoped`, for sub-resource routes."""
    return load_scoped(session, project_id, principal)


def visible_projects(session: Session, principal: Principal) -> list[Project]:
    """Every project this principal may at least read, newest first.

    Listing cannot use :func:`load_scoped` (there is no single id to check), so
    the same predicate is applied row by row. Postgres could push this into the
    WHERE clause; with a single SQLite file the round trip costs more than the
    filter, and keeping one predicate means list and detail cannot disagree.
    """
    rows = session.exec(select(Project).order_by(Project.created_at.desc())).all()  # type: ignore[attr-defined]
    return [row for row in rows if can_access(principal, row.owner_id, row.org_id, row.id)]
