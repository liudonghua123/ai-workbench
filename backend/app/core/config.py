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

# Absolute, so `cp .env.example .env` in the repo root works no matter which
# directory uvicorn was started from (relative paths resolve against the CWD,
# which made the documented `.env` invisible when run from `backend/`).
# Later files win: the repo root is the shared default, `backend/.env` the
# machine-specific override.
ENV_FILES = (str(REPO_ROOT / ".env"), str(REPO_ROOT / "backend" / ".env"))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="WB_",
        env_file=ENV_FILES,
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
    domain_base_url: str = ""  # e.g. https://gateway.example.com; empty -> the built-in gateway
    # debug/preview traffic goes to <slug><preview_suffix>.<domain_suffix>
    preview_suffix: str = "-dev"
    port_range_start: int = 4200
    port_range_end: int = 4399

    # --- gateway (built-in reverse proxy) ------------------------------
    # Projects are reached by Host, not by port: <slug>.<domain_suffix> for
    # production and <slug><preview_suffix>.<domain_suffix> for preview. Both
    # hosts are the *same* listener, which is what makes the URL port-free.
    gateway_enabled: bool = True
    gateway_bind_host: str = "0.0.0.0"
    gateway_http_port: int = 80
    gateway_https_port: int = 443
    # A wildcard certificate is the preferred shape: one file pair covers every
    # project and preview host, nothing is issued at runtime, nothing expires per
    # project. Point both at the fullchain and the private key.
    gateway_tls_cert: str = ""
    gateway_tls_key: str = ""
    # Per-host certificates live here (ACME-issued, or dropped in by hand as
    # <host>.pem + <host>.key). Also holds the ACME account key, its challenges
    # and the issue queue. Empty -> <data_dir>/tls.
    gateway_tls_dir: str = ""
    # http-01 issuance for hosts the wildcard does not cover. Requires port 80 to
    # be reachable from the internet and the host's A record to point here.
    gateway_acme_enabled: bool = False
    gateway_acme_email: str = ""
    gateway_acme_directory: str = "https://acme-v02.api.letsencrypt.org/directory"
    gateway_acme_renew_days: int = 30
    gateway_acme_interval: float = 300.0
    # 80 -> 443 permanent redirect for everything except ACME challenges
    gateway_https_redirect: bool = True
    # upstreams are always loopback: the gateway never becomes an open proxy
    gateway_upstream_host: str = "127.0.0.1"
    # how long a Host -> upstream lookup is cached before the DB is re-read
    gateway_route_ttl: float = 2.0
    # request bodies up to this size are buffered so the upstream receives a
    # Content-Length instead of chunked input (WSGI apps cannot read chunked)
    gateway_body_buffer: int = 1_048_576
    # "http" / "https"; empty -> https when a certificate is configured
    gateway_public_scheme: str = ""

    # --- provider selection (swap a string, get a different cloud) -------
    storage_provider: str = "local"
    database_provider: str = "local"
    domain_provider: str = "local"
    deploy_provider: str = "local"

    # "simulate" writes a realistic build/deploy log without spawning processes
    # "process"  actually runs the project's run command on a local port
    deploy_mode: str = "simulate"

    # How a preview (debug) environment is served:
    # "auto"    -> process when WB_DEPLOY_MODE=process, otherwise the static host
    # "process" -> always try the real dev server first
    # "static"  -> always serve the built output (or the explainer page)
    preview_mode: str = "auto"
    preview_timeout: float = 25.0

    # code-server / openvscode-server entry, e.g. "http://localhost:8443/?folder={path}"
    ide_url_template: str = ""

    # --- agent runtime -------------------------------------------------
    default_agent_model: str = "workbuddy-flash"
    agent_max_steps: int = 24
    # "auto" -> LLM when WB_LLM_API_KEY is set, otherwise the local planner
    # "local" -> always rule-based   "llm" -> always LLM (fail loudly)
    agent_harness: str = "auto"
    # Any OpenAI-compatible /chat/completions endpoint (vLLM, Ollama, DashScope, ...)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout: float = 90.0
    llm_temperature: float = 0.2

    # --- database console ----------------------------------------------
    # "readonly" refuses anything that is not a read statement
    sql_mode: str = "readonly"
    sql_row_limit: int = 500

    # --- auth ----------------------------------------------------------
    # off by default so local development stays frictionless.
    # WB_AUTH_ENABLED=true + WB_AUTH_TOKENS="tok1:alice:admin,tok2:bob:viewer"
    auth_enabled: bool = False
    auth_tokens: str = ""
    # token handed to the auto-provisioned operator account when auth is on
    auth_bootstrap_token: str = "workbench-dev-token"

    # --- identity / SSO ------------------------------------------------
    # where the browser lands after an SSO round trip
    frontend_url: str = "http://localhost:3000"
    # public base of this API, used to build {base}/api/v1/auth/{key}/callback.
    # empty -> derived from the incoming request (correct behind most proxies)
    public_api_base: str = ""
    auth_session_hours: int = 12
    # local email+password sign-up; independent of SSO, so a fresh install is usable
    auth_allow_signup: bool = True
    # slug of the organization that owns projects created before orgs existed
    auth_default_org: str = "default"
    # minutes an in-flight SSO state stays valid
    auth_state_ttl: int = 10

    # --- misc ----------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]
    # In debug the console is allowed on any loopback port, because that is the
    # whole point of a configurable dev port: `npm run dev -- --port 3101` must
    # not fail CORS. Disabled with WB_DEBUG=false, where only cors_origins count.
    cors_allow_loopback: bool = True
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
    def cors_origin_regex(self) -> str:
        """Loopback on any port — dev only, so a moved console port keeps working."""
        if self.debug and self.cors_allow_loopback:
            return r"^https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?$"
        return ""

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def tls_dir(self) -> Path:
        """Where per-host certificates, the ACME account and the queue live."""
        if self.gateway_tls_dir:
            return Path(os.path.expanduser(self.gateway_tls_dir)).resolve()
        return self.data_dir / "tls"

    @property
    def gateway_tls_enabled(self) -> bool:
        """True when the gateway should speak TLS at all.

        A configured wildcard or an enabled ACME client both count. So does a
        directory that already holds certificates: dropping ``<host>.pem`` and
        ``<host>.key`` into it *is* a way to enable TLS, and having the URLs stay
        ``http://`` in that case would be a lie.
        """
        if self.gateway_tls_cert and self.gateway_tls_key:
            return True
        if self.gateway_acme_enabled:
            return True
        try:
            return any(path.suffix == ".pem" for path in self.tls_dir.iterdir())
        except OSError:
            return False

    @property
    def gateway_tls_mode(self) -> str:
        """``manual`` | ``acme`` | ``mixed`` | ``off`` — for the UI and the logs.

        "manual" means "certificates come from files" (a wildcard on the command
        line, or per-host pairs dropped into the directory); "mixed" means files
        *and* an ACME client, which is the recommended shape: the wildcard covers
        everything it can, http-01 fills the gaps.
        """
        files = bool(self.gateway_tls_cert and self.gateway_tls_key)
        acme = bool(self.gateway_acme_enabled)
        if files and acme:
            return "mixed"
        if files:
            return "manual"
        if acme:
            return "mixed" if self._tls_dir_has_certs() else "acme"
        return "manual" if self._tls_dir_has_certs() else "off"

    def _tls_dir_has_certs(self) -> bool:
        try:
            return any(path.suffix == ".pem" for path in self.tls_dir.iterdir())
        except OSError:
            return False

    @property
    def gateway_scheme(self) -> str:
        """Scheme published URLs use when the built-in gateway serves them."""
        if self.gateway_public_scheme:
            return self.gateway_public_scheme
        return "https" if self.gateway_tls_enabled else "http"

    @property
    def gateway_port(self) -> int:
        return self.gateway_https_port if self.gateway_scheme == "https" else self.gateway_http_port

    @property
    def gateway_active(self) -> bool:
        """True when published URLs should be host-only (no port, no per-app socket).

        An explicit ``WB_DOMAIN_BASE_URL`` wins: that means an *external* proxy or
        tunnel terminates traffic and the built-in gateway is not in the path.
        """
        return self.gateway_enabled and not self.domain_base_url

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
