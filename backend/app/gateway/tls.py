"""Certificates for the gateway: one wildcard, or a directory of them, or both.

Two ways to get HTTPS, in the order most installations want them:

**A wildcard certificate** (``*.example.com``) covers every project host and every
preview host after it, forever, with one file pair. Nothing has to be issued at
runtime, nothing expires per project, and the certificate can come from wherever
the operator already buys them. Pass ``--tls-cert`` / ``--tls-key`` (or
``WB_GATEWAY_TLS_CERT/KEY``) and the gateway is done.

**Per-host certificates** (``shop.example.com``) are the fallback when a wildcard
is not available — for example when the domain is behind a CDN that only allows
specific names. Those are issued by :mod:`app.gateway.acme` over http-01 and land
in a directory the gateway scans.

Both live in one :class:`CertStore`. It answers three questions:

* *which certificate do I show for this SNI name?* — exact match, then wildcard,
  then the default (a mismatch warning is honest; refusing the handshake is not);
* *is it still valid?* — expiry is parsed from the certificate, never assumed;
* *did the files change?* — rescanned on a short TTL, so renewals and manual
  rotations take effect without restarting the listener.

The listener is bound with a *bootstrap* self-signed certificate when nothing is
issued yet: the port comes up immediately, http-01 can then complete, and the
first real certificate is picked up on the next handshake. That is what makes
"start the gateway, then get a certificate" a working order of operations.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import ssl
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

logger = logging.getLogger("workbench.gateway.tls")

SAFE_NAME = re.compile(r"[^a-z0-9.-]+")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def normalise(name: str) -> str:
    """Lowercase, no trailing dot, no port — the only form stored anywhere."""
    return (name or "").strip().lower().rstrip(".").split(":", 1)[0]


def covers(names: tuple[str, ...] | list[str], host: str) -> bool:
    """RFC 6125 style match: ``*.example.com`` covers one label, not two."""
    wanted = normalise(host)
    if not wanted:
        return False
    for name in names:
        candidate = normalise(name)
        if not candidate:
            continue
        if candidate == wanted:
            return True
        if candidate.startswith("*."):
            suffix = candidate[2:]
            if wanted.endswith("." + suffix):
                head = wanted[: -(len(suffix) + 1)]
                if head and "." not in head:
                    return True
    return False


def filename_for(host: str) -> str:
    """``*.example.com`` -> ``wildcard.example.com``; never a path traversal."""
    name = normalise(host)
    if name.startswith("*."):
        name = "wildcard." + name[2:]
    return SAFE_NAME.sub("_", name).strip("._") or "unnamed"


def _utc(moment: datetime | None) -> datetime:
    if moment is None:  # pragma: no cover - defensive
        return datetime.now(UTC)
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _issuer_of(cert) -> str:  # noqa: ANN001 - cryptography object, imported lazily
    from cryptography.x509.oid import NameOID

    for oid in (NameOID.ORGANIZATION_NAME, NameOID.COMMON_NAME):
        attributes = cert.issuer.get_attributes_for_oid(oid)
        if attributes:
            return str(attributes[0].value)
    return cert.issuer.rfc4514_string()


def _dns_names(cert) -> tuple[str, ...]:  # noqa: ANN001
    from cryptography import x509
    from cryptography.x509.oid import ExtensionOID, NameOID

    names: list[str] = []
    try:
        san = cert.extensions.get_extension_for_oid(ExtensionOID.SUBJECT_ALTERNATIVE_NAME)
        names = [normalise(item) for item in san.value.get_values_for_type(x509.DNSName)]
    except x509.ExtensionNotFound:
        for attribute in cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME):
            names.append(normalise(str(attribute.value)))
    # de-duplicate, keep order
    seen: dict[str, None] = {}
    for name in names:
        if name:
            seen.setdefault(name, None)
    return tuple(seen)


@dataclass(slots=True)
class Cert:
    """One certificate + key pair, as read from disk."""

    hosts: tuple[str, ...]
    path: Path
    key_path: Path
    source: str  # manual | file | acme | bootstrap
    not_before: datetime
    not_after: datetime
    issuer: str
    serial: str
    fingerprint: str
    key_ok: bool = True
    error: str = ""

    @property
    def is_wildcard(self) -> bool:
        return any(host.startswith("*.") for host in self.hosts)

    @property
    def days_left(self) -> int:
        return int((self.not_after - datetime.now(UTC)).total_seconds() // 86400)

    @property
    def expired(self) -> bool:
        return self.not_after <= datetime.now(UTC)

    def covers(self, host: str) -> bool:
        return not self.error and covers(self.hosts, host)

    def to_public(self) -> dict[str, object]:
        return {
            "hosts": list(self.hosts),
            "path": self.path.as_posix(),
            "source": self.source,
            "issuer": self.issuer,
            "serial": self.serial,
            "fingerprint": self.fingerprint,
            "not_before": self.not_before.isoformat(),
            "not_after": self.not_after.isoformat(),
            "days_left": self.days_left,
            "expired": self.expired,
            "wildcard": self.is_wildcard,
            "key_ok": self.key_ok,
            "error": self.error,
        }


def _read_cert(path: Path, key_path: Path, source: str) -> Cert:
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    # A directory entry is labelled "file" unless its writer left a marker: the
    # difference matters when deciding whether a renewal will overwrite the pair.
    if source == "file":
        meta = path.with_suffix(".meta")
        if meta.is_file():
            try:
                source = str(json.loads(meta.read_text(encoding="utf-8")).get("source") or source)
            except (OSError, ValueError):
                pass

    pem = path.read_bytes()
    certificate = x509.load_pem_x509_certificate(pem)
    hosts = _dns_names(certificate)

    key_ok = not key_path.exists()
    if key_path.exists():
        try:
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            key_ok = (
                key.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                == certificate.public_key().public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
            )
        except (ValueError, TypeError) as exc:
            logger.warning("certificate key unreadable: %s (%s)", key_path, exc)
            key_ok = False

    # cryptography >= 42 exposes timezone-aware fields; older ones do not
    not_before = _utc(
        getattr(certificate, "not_valid_before_utc", None) or certificate.not_valid_before
    )
    not_after = _utc(
        getattr(certificate, "not_valid_after_utc", None) or certificate.not_valid_after
    )

    return Cert(
        hosts=hosts,
        path=path,
        key_path=key_path,
        source=source,
        not_before=not_before,
        not_after=not_after,
        issuer=_issuer_of(certificate),
        serial=format(certificate.serial_number, "x")[-16:],
        fingerprint=hashlib.sha256(pem).hexdigest()[:16],
        key_ok=key_ok,
    )


def _ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(cert_path), str(key_path))
    context.set_alpn_protocols(["http/1.1"])
    return context


# ---------------------------------------------------------------------------
# the store
# ---------------------------------------------------------------------------


class CertStore:
    """Everything the listener needs to know about certificates, on disk."""

    def __init__(
        self,
        directory: Path,
        *,
        cert_file: str = "",
        key_file: str = "",
        ttl: float = 3.0,
    ) -> None:
        self.directory = Path(directory)
        self.cert_file = (cert_file or "").strip()
        self.key_file = (key_file or "").strip()
        self._ttl = ttl
        self._lock = threading.Lock()
        self._certs: list[Cert] = []
        self._loaded_at = 0.0
        self._stamp: tuple[float, ...] = ()
        self._contexts: dict[tuple[str, float, float], ssl.SSLContext] = {}
        self._bootstrap: ssl.SSLContext | None = None
        self._bootstrap_expires: datetime | None = None

    # -- scanning --------------------------------------------------------
    def reload(self) -> None:
        with self._lock:
            self._loaded_at = 0.0
        self._ensure()

    def entries(self) -> list[Cert]:
        self._ensure()
        return list(self._certs)

    def _paths(self) -> list[tuple[Path, Path, str]]:
        pairs: list[tuple[Path, Path, str]] = []
        if self.cert_file:
            cert_path = Path(self.cert_file)
            key_path = (
                Path(self.key_file)
                if self.key_file
                else Path(str(cert_path).replace(".pem", ".key"))
            )
            pairs.append((cert_path, key_path, "manual"))
        try:
            found = sorted(self.directory.glob("*.pem"))
        except OSError:
            found = []
        for cert_path in found:
            key_path = cert_path.with_suffix(".key")
            pairs.append((cert_path, key_path, "file"))
        # a certificate listed twice (e.g. inside the directory *and* as --tls-cert)
        unique: dict[str, tuple[Path, Path, str]] = {}
        for cert_path, key_path, source in pairs:
            identity = str(cert_path.resolve() if cert_path.exists() else cert_path)
            unique.setdefault(identity, (cert_path, key_path, source))
        return list(unique.values())

    def _stamp_of(self, pairs: list[tuple[Path, Path, str]]) -> tuple[float, ...]:
        stamps: list[float] = []
        for cert_path, key_path, _ in pairs:
            for path in (cert_path, key_path):
                try:
                    stamps.append(path.stat().st_mtime)
                except OSError:
                    stamps.append(0.0)
        return tuple(stamps)

    def _ensure(self) -> None:
        if time.monotonic() - self._loaded_at < self._ttl:
            return
        with self._lock:
            if time.monotonic() - self._loaded_at < self._ttl:
                return
            pairs = self._paths()
            stamp = self._stamp_of(pairs)
            if stamp != self._stamp:
                loaded: list[Cert] = []
                for cert_path, key_path, source in pairs:
                    if not cert_path.exists():
                        continue
                    try:
                        loaded.append(_read_cert(cert_path, key_path, source))
                    except Exception as exc:  # noqa: BLE001 - a broken file must not stop the rest
                        logger.warning("cannot read certificate %s: %s", cert_path, exc)
                        loaded.append(
                            Cert(
                                hosts=(),
                                path=cert_path,
                                key_path=key_path,
                                source=source,
                                not_before=datetime.now(UTC),
                                not_after=datetime.now(UTC),
                                issuer="",
                                serial="",
                                fingerprint="",
                                key_ok=False,
                                error=str(exc),
                            )
                        )
                self._certs = loaded
                self._stamp = stamp
            self._loaded_at = time.monotonic()

    # -- selection -------------------------------------------------------
    def resolve(self, host: str) -> Cert | None:
        """Best certificate for a host: exact, then wildcard, then None."""
        wanted = normalise(host)
        candidates = [cert for cert in self.entries() if cert.key_ok and not cert.error]
        for cert in candidates:
            if wanted in cert.hosts:
                return cert
        for cert in candidates:
            if cert.covers(wanted):
                return cert
        return None

    def default(self) -> Cert | None:
        """The certificate to bind the listener with, before any SNI arrives."""
        candidates = [cert for cert in self.entries() if cert.key_ok and not cert.error]
        if not candidates:
            return None
        wildcard = [cert for cert in candidates if cert.is_wildcard]
        pool = wildcard or candidates
        return max(pool, key=lambda cert: len(cert.hosts), default=None)

    def covers_suffix(self, suffix: str) -> bool:
        return any(cert.covers(suffix) or cert.covers("*." + suffix) for cert in self.entries())

    # -- contexts --------------------------------------------------------
    def _context(self, cert: Cert) -> ssl.SSLContext:
        try:
            stat = cert.path.stat()
            key_stat = cert.key_path.stat()
            signature = (stat.st_mtime, key_stat.st_mtime)
        except OSError:
            signature = (0.0, 0.0)
        cache_key = (str(cert.path), *signature)
        cached = self._contexts.get(cache_key)
        if cached is not None:
            return cached
        context = _ssl_context(cert.path, cert.key_path)
        # keep the cache bounded: a rotated certificate is a new key, not an edit
        if len(self._contexts) > 32:
            self._contexts.clear()
        self._contexts[cache_key] = context
        return context

    def _bootstrap_context(self) -> ssl.SSLContext:
        """A throwaway self-signed certificate so 443 can bind before issuance."""
        if self._bootstrap is not None and (
            self._bootstrap_expires is None or self._bootstrap_expires > datetime.now(UTC)
        ):
            return self._bootstrap

        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        from app.core.config import settings

        suffix = normalise(settings.domain_suffix) or "localhost"
        holder = self.directory / ".bootstrap"
        holder.mkdir(parents=True, exist_ok=True)
        cert_path = holder / "bootstrap.pem"
        key_path = holder / "bootstrap.key"

        now = datetime.now(UTC)
        fresh = False
        if cert_path.exists() and key_path.exists():
            try:
                existing = _read_cert(cert_path, key_path, "bootstrap")
                fresh = existing.key_ok and existing.not_after > now + timedelta(days=2)
            except Exception:  # noqa: BLE001 - regenerate anything unreadable
                fresh = False
        if not fresh:
            key = ec.generate_private_key(ec.SECP256R1())
            name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "*." + suffix)])
            certificate = (
                x509.CertificateBuilder()
                .subject_name(name)
                .issuer_name(name)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(now - timedelta(hours=1))
                .not_valid_after(now + timedelta(days=30))
                .add_extension(
                    x509.SubjectAlternativeName(
                        [x509.DNSName("*." + suffix), x509.DNSName(suffix)]
                    ),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            _write_atomic(
                key_path,
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
                0o600,
            )
            _write_atomic(cert_path, certificate.public_bytes(serialization.Encoding.PEM), 0o644)
            logger.info("generated a bootstrap self-signed certificate for *.%s", suffix)

        context = _ssl_context(cert_path, key_path)
        self._bootstrap = context
        self._bootstrap_expires = datetime.now(UTC) + timedelta(hours=12)
        return context

    def server_context(self) -> ssl.SSLContext:
        """The context uvicorn binds 443 with; SNI picks the real certificate."""
        primary = self.default()
        base = self._context(primary) if primary is not None else self._bootstrap_context()
        base.set_servername_callback(self._sni)
        return base

    def _sni(self, sslobj, server_name, _ctx) -> None:  # noqa: ANN001 - stdlib callback
        """Swap in the certificate that actually matches the requested name."""
        host = normalise(server_name or "")
        if not host:
            return
        cert = self.resolve(host)
        if cert is None:
            return  # keep the default: the browser shows a mismatch, not a dead port
        try:
            sslobj.context = self._context(cert)
        except (OSError, ssl.SSLError) as exc:  # pragma: no cover - rotation race
            logger.warning("cannot switch certificate for %s: %s", host, exc)

    # -- writing ---------------------------------------------------------
    def store(self, host: str, chain_pem: bytes, key_pem: bytes, *, source: str = "acme") -> Cert:
        """Persist a freshly issued certificate; the next handshake uses it."""
        self.directory.mkdir(parents=True, exist_ok=True)
        stem = filename_for(host)
        cert_path = self.directory / (stem + ".pem")
        key_path = self.directory / (stem + ".key")
        _write_atomic(key_path, key_pem, 0o600)
        _write_atomic(cert_path, chain_pem, 0o644)
        stamp = datetime.now(UTC).isoformat()
        _write_atomic(
            cert_path.with_suffix(".meta"),
            json.dumps(
                {"host": host, "source": source, "written_at": stamp}, ensure_ascii=False
            ).encode(),
            0o644,
        )
        self.reload()
        record = _read_cert(cert_path, key_path, source)
        logger.info(
            "stored certificate for %s (%s, %s days left)",
            host,
            record.issuer,
            record.days_left,
        )
        return record

    # -- reporting -------------------------------------------------------
    def status(self) -> dict[str, object]:
        certs = self.entries()
        usable = [cert for cert in certs if cert.key_ok and not cert.error]
        primary = self.default()
        soonest = min(usable, key=lambda cert: cert.not_after, default=None)
        return {
            "directory": self.directory.as_posix(),
            "certs": [cert.to_public() for cert in certs],
            "count": len(certs),
            "usable": len(usable),
            "primary": primary.to_public() if primary is not None else None,
            "bootstrap": primary is None,
            "expired": sum(1 for cert in certs if cert.expired),
            "expiring": sum(1 for cert in usable if cert.days_left <= 14),
            "days_left": soonest.days_left if soonest is not None else None,
        }


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, mode)
    except OSError:  # pragma: no cover - Windows
        pass
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# csr / key helpers shared with the ACME client
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class KeyPair:
    """A private key plus its CSR, produced locally and never sent as a key."""

    key_pem: bytes
    csr_der: bytes
    extra: dict[str, str] = field(default_factory=dict)


def build_csr(hosts: list[str]) -> KeyPair:
    """ECDSA P-256 key + CSR covering every host name."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    names = [normalise(host) for host in hosts if normalise(host)]
    if not names:
        raise ValueError("没有要签发的域名")

    key = ec.generate_private_key(ec.SECP256R1())
    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0][:64])])
    )
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.DNSName(name) for name in names]), critical=False
    )
    csr = builder.sign(key, hashes.SHA256())
    return KeyPair(
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
        csr_der=csr.public_bytes(serialization.Encoding.DER),
    )


def self_signed(hosts: list[str], days: int = 30) -> tuple[bytes, bytes]:
    """A certificate for tests and for the bootstrap context."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    names = [normalise(host) for host in hosts if normalise(host)]
    key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, names[0] if names else "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=days))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName(n) for n in names]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
