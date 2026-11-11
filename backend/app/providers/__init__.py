"""Provider package — import side effects register the available adapters."""

from __future__ import annotations

import app.providers.local  # noqa: F401  (registers local:// adapters)
from app.core.config import settings
from app.providers.base import (
    BucketInfo,
    DatabaseInfo,
    DatabaseProvider,
    DeployProvider,
    DeployResult,
    DeploySpec,
    DomainInfo,
    DomainProvider,
    Provider,
    ProviderRegistry,
    StorageProvider,
    register_provider,
    registry,
)

__all__ = [
    "BucketInfo",
    "DatabaseInfo",
    "DatabaseProvider",
    "DeployProvider",
    "DeployResult",
    "DeploySpec",
    "DomainInfo",
    "DomainProvider",
    "Provider",
    "ProviderRegistry",
    "StorageProvider",
    "database_provider",
    "deploy_provider",
    "domain_provider",
    "register_provider",
    "registry",
    "storage_provider",
]


def storage_provider() -> StorageProvider:
    return registry.get("storage", settings.storage_provider)  # type: ignore[return-value]


def database_provider() -> DatabaseProvider:
    return registry.get("database", settings.database_provider)  # type: ignore[return-value]


def domain_provider() -> DomainProvider:
    return registry.get("domain", settings.domain_provider)  # type: ignore[return-value]


def deploy_provider() -> DeployProvider:
    return registry.get("deploy", settings.deploy_provider)  # type: ignore[return-value]
