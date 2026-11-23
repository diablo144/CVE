"""Runtime configuration for the ReconRelay gateway.

Every knob is an environment variable prefixed with ``RR_`` so the same image
can be run in dev (memory store, inline worker) and on the Oracle box
(redis store, separate worker containers) without code changes.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.replace("\n", ",").split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RR_", extra="ignore")

    app_name: str = "reconrelay"
    debug: bool = False

    # ------------------------------------------------------------------ auth
    jwt_secret: str = "change-me-before-deploying"
    jwt_algorithm: str = "HS256"
    jwt_ttl_seconds: int = 8 * 60 * 60
    # Comma separated keys handed to the browser extension. Each one identifies
    # an operator; the extension exchanges it for a short lived JWT.
    api_keys: str = "dev-key-0000"

    # ------------------------------------------------------------- scan policy
    # The allowlist is the single most important control in this project: it is
    # what stops the extension from turning your server into an open scanning
    # proxy. Leave empty + allow_any_scope=False and every job is rejected.
    authorized_scopes: str = ""
    allow_any_scope: bool = False
    default_preset: str = "recon"
    max_events_per_scan: int = 5000
    max_depth: int = 4
    scan_timeout_seconds: int = 900
    # Active modules open sockets against the target. They are opt-in per job
    # *and* require this global switch, so a leaked extension cannot escalate a
    # passive scan into a port scan.
    allow_active_modules: bool = False

    # --------------------------------------------------------------- backends
    redis_url: str = "redis://localhost:6379/0"
    # Memory store + inline worker: zero infrastructure, ideal for local dev
    # and for the test suite (which exercises the real code paths).
    use_memory_store: bool = True
    inline_worker: bool = True
    worker_concurrency: int = 1
    worker_poll_interval: float = 0.25

    cors_origins: str = "chrome-extension://*"
    cors_origin_regex: str = r"^chrome-extension://[a-p]{32}$"

    # ------------------------------------------------------------- validators
    @field_validator("authorized_scopes", "api_keys", "cors_origins")
    @classmethod
    def _normalise_csv(cls, value: str) -> str:
        return ",".join(_split_csv(value))

    @property
    def api_key_set(self) -> set[str]:
        return set(_split_csv(self.api_keys))

    @property
    def scope_list(self) -> list[str]:
        return _split_csv(self.authorized_scopes)

    @property
    def cors_origin_list(self) -> list[str]:
        return _split_csv(self.cors_origins)


@lru_cache
def get_settings() -> Settings:
    return Settings()
