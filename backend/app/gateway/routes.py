"""Host -> upstream routing table.

The control plane owns the truth (the ``projects`` table); the gateway only reads
it. Rebuilding is a single cheap query, so the table is cached for a few seconds
instead of being pushed around by events — that keeps the gateway a *separate*
process with no shared mutable state, and a new project is routable within
``WB_GATEWAY_ROUTE_TTL`` seconds without a restart.

Only loopback upstreams are ever produced. A route cannot be created from
request data (no user-supplied host, no user-supplied port), so the gateway is
not an open proxy: a Host header can only ever reach a port the control plane
itself allocated for that project.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from sqlmodel import Session, select

from app.core.config import settings
from app.core.db import engine
from app.models import DomainBinding, Project, ProjectStatus
from app.services import conventions

PREVIEW = "preview"
PRODUCTION = "production"


def normalize_host(value: str) -> str:
    """``Customer-Success.Localtest.me:8080.`` -> ``customer-success.localtest.me``."""
    host = (value or "").strip().lower().rstrip(".")
    if host.startswith("["):  # IPv6 literal: keep the brackets, drop the port
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.split(":", 1)[0]


@dataclass(frozen=True, slots=True)
class Route:
    host: str
    project_id: str
    slug: str
    name: str
    environment: str
    port: int
    project_status: str = ProjectStatus.READY
    custom: bool = False

    @property
    def is_preview(self) -> bool:
        return self.environment == PREVIEW

    @property
    def upstream(self) -> str:
        return f"http://{settings.gateway_upstream_host}:{self.port}"

    def to_public(self) -> dict[str, object]:
        return {
            "host": self.host,
            "url": conventions.origin_for(self.host, self.port, via_gateway=True),
            "project_id": self.project_id,
            "slug": self.slug,
            "name": self.name,
            "environment": self.environment,
            "upstream": self.upstream,
            "custom": self.custom,
        }


class RouteTable:
    """Cached view of every addressable host. Cheap to rebuild, safe to share."""

    def __init__(self, ttl: float | None = None) -> None:
        self._ttl = settings.gateway_route_ttl if ttl is None else ttl
        self._lock = threading.Lock()
        self._routes: dict[str, Route] = {}
        self._loaded_at = 0.0

    # -- lookup ----------------------------------------------------------
    def resolve(self, host: str) -> Route | None:
        key = normalize_host(host)
        if not key:
            return None
        self._ensure_fresh()
        return self._routes.get(key)

    def snapshot(self) -> list[Route]:
        self._ensure_fresh()
        return sorted(self._routes.values(), key=lambda r: (r.slug, r.environment))

    def invalidate(self) -> None:
        with self._lock:
            self._loaded_at = 0.0

    # -- load ------------------------------------------------------------
    def _ensure_fresh(self) -> None:
        if time.monotonic() - self._loaded_at < self._ttl:
            return
        with self._lock:
            # another coroutine may have refreshed while we waited for the lock
            if time.monotonic() - self._loaded_at < self._ttl:
                return
            try:
                self._routes = self._load()
            except Exception:  # noqa: BLE001 - a DB hiccup must not kill the proxy
                # keep the previous table rather than 502-ing every host
                pass
            self._loaded_at = time.monotonic()

    def _load(self) -> dict[str, Route]:
        routes: dict[str, Route] = {}
        with Session(engine) as session:
            projects = session.exec(select(Project)).all()
            bindings = session.exec(select(DomainBinding)).all()

        by_id = {project.id: project for project in projects}
        for project in projects:
            if project.status == ProjectStatus.ARCHIVED:
                continue
            if project.port:
                host = normalize_host(conventions.production_fqdn(project))
                routes[host] = Route(
                    host=host,
                    project_id=project.id,
                    slug=project.slug,
                    name=project.name,
                    environment=PRODUCTION,
                    port=project.port,
                    project_status=project.status,
                )
            preview_host = normalize_host(
                project.preview_fqdn or conventions.preview_fqdn(project)
            )
            if project.dev_port and preview_host:
                routes[preview_host] = Route(
                    host=preview_host,
                    project_id=project.id,
                    slug=project.slug,
                    name=project.name,
                    environment=PREVIEW,
                    port=project.dev_port,
                    project_status=project.status,
                )

        # A custom domain bound through the domain provider must work the same
        # way; the preview host is the only binding that is not production.
        for binding in bindings:
            project = by_id.get(binding.project_id)
            if project is None or project.status == ProjectStatus.ARCHIVED:
                continue
            host = normalize_host(binding.fqdn)
            if not host or host in routes:
                continue
            is_preview = host == normalize_host(
                project.preview_fqdn or conventions.preview_fqdn(project)
            )
            port = project.dev_port if is_preview else project.port
            if not port:
                continue
            routes[host] = Route(
                host=host,
                project_id=project.id,
                slug=project.slug,
                name=project.name,
                environment=PREVIEW if is_preview else PRODUCTION,
                port=port,
                project_status=project.status,
                custom=True,
            )
        return routes


table = RouteTable()
