"""Built-in reverse proxy: ``python -m app.gateway``.

The route table lives in :mod:`app.gateway.routes` and has no heavy dependencies —
the control plane imports it for ``GET /system/gateway`` without dragging the
proxy (and ``httpx``/``websockets``) into its own process. That is why
:func:`create_app` imports the ASGI app lazily.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.gateway.routes import Route, RouteTable, normalize_host
from app.gateway.routes import table as route_table

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.gateway.proxy import Gateway

__all__ = ["Route", "RouteTable", "create_app", "normalize_host", "route_table"]


def create_app(*args: Any, **kwargs: Any) -> Gateway:
    """Build the gateway ASGI application (imports the proxy on demand)."""
    from app.gateway.proxy import create_app as build

    return build(*args, **kwargs)
