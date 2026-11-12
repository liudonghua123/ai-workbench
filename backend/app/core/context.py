"""Current-request identity, available to code that has no ``Request``.

The agent tools run inside a request but are handed only services, not the HTTP
layer. A ``ContextVar`` keeps them from either re-parsing headers or, worse,
silently running as an all-powerful system account.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.core.security import Principal

_current: ContextVar["Principal | None"] = ContextVar("wb_principal", default=None)
_client_ip: ContextVar[str] = ContextVar("wb_client_ip", default="")


def set_principal(principal: "Principal | None"):
    return _current.set(principal)


def reset_principal(token) -> None:
    try:
        _current.reset(token)
    except ValueError:  # different context — nothing to restore
        pass


def current_principal() -> "Principal | None":
    return _current.get()


def set_client_ip(ip: str):
    return _client_ip.set(ip or "")


def reset_client_ip(token) -> None:
    try:
        _client_ip.reset(token)
    except ValueError:
        pass


def current_client_ip() -> str:
    """Source address of the request in flight, for the audit trail."""
    return _client_ip.get()
