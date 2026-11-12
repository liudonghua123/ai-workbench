"""Certificate reporting for the control plane.

The gateway owns TLS: it reads the certificates, answers the challenges and
renews them. The control plane never touches a private key — it reads the same
directory and reports what it sees, and it *asks* for issuance by dropping a file
in the queue. That asymmetry is deliberate: two processes writing certificates is
how you end up with a half-renewed pair and a listener that cannot handshake.

What the UI needs to know, in one payload:

* which mode is configured (a wildcard certificate, ACME, both, or nothing);
* what is actually on disk, per host: issuer, expiry, whether the key matches;
* which routed hosts have **no** certificate — the actionable list;
* what is pending in the queue, and why the last attempt failed.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from pathlib import Path

from app.core.config import settings
from app.gateway import tls as tls_mod
from app.gateway.acme import AcmeError, IssueQueue

_CACHE: dict[str, tuple[float, tls_mod.CertStore]] = {}
_TTL = 2.0


def _store() -> tls_mod.CertStore:
    key = str(settings.tls_dir)
    cached = _CACHE.get(key)
    now = time.monotonic()
    if cached is not None and now - cached[0] < _TTL:
        return cached[1]
    store = tls_mod.CertStore(
        settings.tls_dir,
        cert_file=settings.gateway_tls_cert,
        key_file=settings.gateway_tls_key,
    )
    _CACHE[key] = (now, store)
    return store


def queue() -> IssueQueue:
    return IssueQueue(settings.tls_dir / "queue")


def reload() -> None:
    """Force the next status read to rescan the directory."""
    _CACHE.pop(str(settings.tls_dir), None)


def _hosts_of(hosts: Iterable[str]) -> Iterator[str]:
    seen: set[str] = set()
    for host in hosts:
        name = tls_mod.normalise(host)
        if name and name not in seen:
            seen.add(name)
            yield name


def summary(hosts: Iterable[str] = ()) -> dict[str, object]:
    """Certificate state for the settings page and for the CLI."""
    store = _store()
    status = store.status()
    routed = list(_hosts_of(hosts))
    missing: list[str] = []
    expiring: list[str] = []
    for host in routed:
        cert = store.resolve(host)
        if cert is None or cert.expired or not cert.key_ok:
            missing.append(host)
        elif cert.days_left <= 14:
            expiring.append(host)

    primary = status.get("primary")
    wildcard = bool(isinstance(primary, dict) and primary.get("wildcard"))
    covered_by_suffix = store.covers_suffix(tls_mod.normalise(settings.domain_suffix))
    mode = settings.gateway_tls_mode
    if mode == "manual" and not wildcard:
        # certificates are files, but none of them is a wildcard: the UI must not
        # claim "one cert covers everything" when it does not
        mode = "files"

    return {
        "mode": mode,
        "enabled": settings.gateway_tls_enabled,
        "scheme": settings.gateway_scheme,
        "port": settings.gateway_port,
        "suffix": settings.domain_suffix,
        "wildcard": wildcard,
        "suffix_covered": covered_by_suffix,
        "certificate": status,
        "acme": {
            "enabled": settings.gateway_acme_enabled,
            "email": settings.gateway_acme_email,
            "directory": settings.gateway_acme_directory,
            "renew_days": settings.gateway_acme_renew_days,
            "staging": "staging" in settings.gateway_acme_directory,
        },
        "routed": len(routed),
        "missing": missing,
        "expiring": expiring,
        "queue": queue().listing(),
    }


def request_issue(host: str, *, allowed: Iterable[str] | None = None) -> dict[str, object]:
    """Queue one host for issuance. Rejects wildcards and unknown hosts."""
    host = tls_mod.normalise(host)
    if not host:
        raise AcmeError("域名不能为空")
    if "*" in host:
        raise AcmeError(
            "通配符不能用 http-01 签发：请配置一张 *.域名的证书（优先），"
            "自动签发只对逐个域名生效。"
        )
    if allowed is not None and host not in set(allowed):
        raise AcmeError(f"{host} 不在网关的路由表里，先绑定域名或部署项目")
    if not settings.gateway_acme_enabled:
        raise AcmeError("自动签发未启用：设置 WB_GATEWAY_ACME_ENABLED=true 后重启网关")
    record = queue().request(host)
    return record.to_public()


def cancel_issue(host: str) -> bool:
    return queue().forget(tls_mod.normalise(host))


def queue_dir() -> Path:
    return settings.tls_dir / "queue"
