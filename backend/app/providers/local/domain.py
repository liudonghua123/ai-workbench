from __future__ import annotations

import json
import socket
from typing import Any

from app.core.config import settings
from app.providers.base import DomainInfo, DomainProvider, register_provider

WILDCARD_ZONES = {"localtest.me", "lacolhost.com", "nip.io", "sslip.io", "127.0.0.1.nip.io"}


@register_provider("domain", "local")
class LocalDomainProvider(DomainProvider):
    """Keeps a JSON zone file and truthfully reports whether the name resolves.

    ``*.localtest.me`` (and nip.io / sslip.io) are public wildcard DNS that always
    answer 127.0.0.1, so auto-issued ``<project>.localtest.me`` names are reachable
    on the developer's machine without touching any registrar.
    """

    name = "local"

    def _zone_path(self):
        settings.dns_dir.mkdir(parents=True, exist_ok=True)
        return settings.dns_dir / "zone.json"

    def _read_zone(self) -> dict[str, Any]:
        path = self._zone_path()
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_zone(self, zone: dict[str, Any]) -> None:
        self._zone_path().write_text(json.dumps(zone, indent=2, ensure_ascii=False), encoding="utf-8")

    def _suffix_of(self, fqdn: str) -> str:
        parts = fqdn.split(".", 1)
        return parts[1] if len(parts) > 1 else ""

    def _resolves(self, fqdn: str) -> bool:
        try:
            socket.setdefaulttimeout(1.0)
            socket.getaddrinfo(fqdn, None)
            return True
        except OSError:
            return False
        finally:
            socket.setdefaulttimeout(None)

    def bind(self, fqdn: str, *, target: str, project_id: str) -> DomainInfo:
        zone = self._read_zone()
        suffix = self._suffix_of(fqdn)
        record = {
            "fqdn": fqdn,
            "type": "CNAME",
            "target": target,
            "project_id": project_id,
            "suffix": suffix,
            "wildcard": suffix in WILDCARD_ZONES,
        }
        zone[fqdn] = record
        self._write_zone(zone)
        status = "active" if suffix in WILDCARD_ZONES or self._resolves(fqdn) else "pending"
        return DomainInfo(
            fqdn=fqdn,
            provider=self.name,
            status=status,
            target=target,
            records=[record],
        )

    def verify(self, fqdn: str) -> DomainInfo:
        zone = self._read_zone()
        record = zone.get(fqdn)
        if record is None:
            return DomainInfo(fqdn=fqdn, provider=self.name, status="error", target="")
        suffix = record.get("suffix", self._suffix_of(fqdn))
        resolved = self._resolves(fqdn)
        status = "active" if resolved or suffix in WILDCARD_ZONES else "pending"
        return DomainInfo(
            fqdn=fqdn,
            provider=self.name,
            status=status,
            target=record.get("target", ""),
            records=[record],
        )

    def unbind(self, fqdn: str) -> None:
        zone = self._read_zone()
        zone.pop(fqdn, None)
        self._write_zone(zone)
