"""Runtime configuration.

Every knob is env-overridable with the ``WB_`` prefix, e.g. ``WB_DOMAIN_SUFFIX=example.dev``.
Defaults are tuned for local-first development: no cloud credentials required.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WB_",
        env_file=(".env", "../../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AI Workbench"
    api_prefix: str = "/api/v1"
    debug: bool = True

    # --- storage -------------------------------------------------------
    data_dir: Path = Field(default=REPO_ROOT / "data")
    database_url: str = ""

    # --- domains -------------------------------------------------------
    # localtest.me / nip.io / sslip.io all resolve *.wildcard -> 127.0.0.1
    domain_suffix: str = "localtest.me"
    domain_base_url: str = ""  # e.g. https://gateway.example.com; empty -> http://<fqdn>:<port>
    port_range_start: int = 4200
    port_range_end: int = 4399

    # --- provider selection (swap a string, get a different cloud) -------
    storage_provider: str = "local"
    database_provider: str = "local"
    domain_provider: str = "local"
    deploy_provider: str = "local"

    # "simulate" writes a realistic build/deploy log without spawning processes
    # "process"  actually runs the project's run command on a local port
    deploy_mode: str = "simulate"

    # code-server / openvscode-server entry, e.g. "http://localhost:8443/?folder={path}"
    ide_url_template: str = ""

    # --- agent runtime -------------------------------------------------
    default_agent_model: str = "workbuddy-flash"
    agent_max_steps: int = 24

    # --- misc ----------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    bootstrap_demo_data: bool = True
    mcp_path: str = "/mcp"

    @field_validator("data_dir", mode="before")
    @classmethod
    def _expand(cls, v: str | Path) -> Path:
        return Path(os.path.expanduser(str(v))).resolve()

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def storage_dir(self) -> Path:
        return self.data_dir / "storage"

    @property
    def db_provider_dir(self) -> Path:
        return self.data_dir / "databases"

    @property
    def dns_dir(self) -> Path:
        return self.data_dir / "dns"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.projects_dir,
            self.storage_dir,
            self.db_provider_dir,
            self.dns_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'workbench.db').as_posix()}"


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


settings = get_settings()
