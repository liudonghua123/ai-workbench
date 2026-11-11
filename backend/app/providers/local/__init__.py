"""Local (no-credentials) provider implementations.

These are deliberately boring: files on disk, sqlite files, a JSON zone file and
local child processes. Their value is that they satisfy the exact same contracts
real cloud adapters do, so the whole product is demoable and testable offline.
"""

from app.providers.local.database import LocalDatabaseProvider
from app.providers.local.deploy import LocalDeployProvider
from app.providers.local.domain import LocalDomainProvider
from app.providers.local.storage import LocalStorageProvider

__all__ = [
    "LocalDatabaseProvider",
    "LocalDeployProvider",
    "LocalDomainProvider",
    "LocalStorageProvider",
]
