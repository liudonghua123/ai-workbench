"""Provider contracts — the seam between the control plane and real infrastructure.

The control plane never talks to a cloud SDK directly. It asks the registry for the
provider configured for a capability (``WB_STORAGE_PROVIDER=s3``) and talks to the
abstract interface. Swapping clouds therefore means adding one module and one string
in the env file; no service or API code changes.

Capabilities
------------
storage   object buckets per project
database  managed SQL databases per project
domain    wildcard subdomain allocation + DNS records
deploy    build & run a project somewhere addressable
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# value objects
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BucketInfo:
    name: str
    provider: str
    endpoint: str = ""
    region: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DatabaseInfo:
    name: str
    provider: str
    engine: str = "sqlite"
    dsn: str = ""
    endpoint: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DomainInfo:
    fqdn: str
    provider: str
    status: str = "pending"
    target: str = ""
    records: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class DeploySpec:
    deployment_id: str
    project_slug: str
    repo_path: Path
    runtime: str
    run_command: str
    port: int
    environment: str = "production"
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class DeployResult:
    status: str
    url: str = ""
    error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


LogSink = Callable[[str], Awaitable[None] | None]


# ---------------------------------------------------------------------------
# interfaces
# ---------------------------------------------------------------------------


class Provider(ABC):
    """Common marker: every provider declares a unique ``name``."""

    name: str = "abstract"
    capability: str = "abstract"


class StorageProvider(Provider):
    capability = "storage"

    @abstractmethod
    def create_bucket(self, bucket: str, *, project_id: str) -> BucketInfo: ...

    @abstractmethod
    def delete_bucket(self, bucket: str) -> None: ...

    @abstractmethod
    def put_object(
        self, bucket: str, key: str, data: bytes, *, content_type: str = "application/octet-stream"
    ) -> dict[str, Any]: ...

    @abstractmethod
    def get_object(self, bucket: str, key: str) -> bytes: ...

    @abstractmethod
    def list_objects(self, bucket: str, *, prefix: str = "") -> list[dict[str, Any]]: ...

    @abstractmethod
    def presign(self, bucket: str, key: str, *, expires_in: int = 3600) -> str: ...


class DatabaseProvider(Provider):
    capability = "database"

    @abstractmethod
    def create_database(self, name: str, *, project_id: str) -> DatabaseInfo: ...

    @abstractmethod
    def drop_database(self, name: str) -> None: ...

    @abstractmethod
    def describe(self, name: str) -> DatabaseInfo: ...

    @abstractmethod
    def execute(self, name: str, sql: str, *, limit: int = 200) -> list[dict[str, Any]]: ...


class DomainProvider(Provider):
    capability = "domain"

    @abstractmethod
    def bind(self, fqdn: str, *, target: str, project_id: str) -> DomainInfo: ...

    @abstractmethod
    def verify(self, fqdn: str) -> DomainInfo: ...

    @abstractmethod
    def unbind(self, fqdn: str) -> None: ...


class DeployProvider(Provider):
    capability = "deploy"

    @abstractmethod
    async def deploy(self, spec: DeploySpec, sink: LogSink) -> DeployResult: ...

    @abstractmethod
    async def stop(self, spec: DeploySpec) -> None: ...


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------

_KINDS = ("storage", "database", "domain", "deploy")


class ProviderRegistry:
    def __init__(self) -> None:
        self._factories: dict[str, dict[str, Callable[[], Provider]]] = {k: {} for k in _KINDS}
        self._instances: dict[tuple[str, str], Provider] = {}

    def register(self, capability: str, name: str) -> Callable[[type[Provider]], type[Provider]]:
        if capability not in self._factories:
            raise ValueError(f"unknown capability: {capability}")

        def _wrap(cls: type[Provider]) -> type[Provider]:
            self._factories[capability][name] = cls
            return cls

        return _wrap

    def available(self, capability: str) -> list[str]:
        return sorted(self._factories.get(capability, {}))

    def get(self, capability: str, name: str) -> Provider:
        key = (capability, name)
        if key in self._instances:
            return self._instances[key]
        factory = self._factories.get(capability, {}).get(name)
        if factory is None:
            raise KeyError(
                f"provider '{name}' is not registered for capability '{capability}'. "
                f"available: {self.available(capability)}"
            )
        instance = factory()
        self._instances[key] = instance
        return instance


registry = ProviderRegistry()


def register_provider(
    capability: str, name: str
) -> Callable[[type[Provider]], type[Provider]]:
    """Decorator used by provider modules: ``@register_provider("storage", "local")``."""
    return registry.register(capability, name)
