"""Small helpers shared by services: ids, slugs, ports."""

from __future__ import annotations

import re
import secrets
import socket
import string

from app.core.config import settings
from app.core.errors import ConflictError

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def slugify(value: str) -> str:
    """ASCII-safe slug. Returns "" when nothing survives (e.g. a pure CJK name) so the
    caller can decide between an explicit slug and a random one."""
    slug = _SLUG_RE.sub("-", value.strip().lower().replace("_", "-")).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    if not slug:
        return ""
    if slug[0].isdigit():
        slug = f"app-{slug}"
    return slug[:40]


def random_suffix(n: int = 4) -> str:
    return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))


def port_available(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def allocate_port(taken: set[int]) -> int:
    for port in range(settings.port_range_start, settings.port_range_end + 1):
        if port in taken or not port_available(port):
            continue
        return port
    raise ConflictError("本地端口段已用尽，请调整 WB_PORT_RANGE_START / WB_PORT_RANGE_END")
