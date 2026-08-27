"""Application settings, loaded from environment / .env."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- LinkedIn session -------------------------------------------------
    linkedin_li_at: str = ""
    linkedin_jsessionid: str = ""
    linkedin_bcookie: str = ""
    linkedin_lidc: str = ""

    # --- Access control ---------------------------------------------------
    api_keys: str = ""
    cors_origins: str = "*"

    # --- Behaviour --------------------------------------------------------
    cache_ttl_seconds: int = 900
    cache_max_entries: int = 512
    rate_limit: str = "30/minute"
    request_timeout_seconds: float = 25.0
    max_retries: int = 3
    min_request_delay: float = 0.4
    max_request_delay: float = 1.2
    user_agent: str = ""

    # --- App --------------------------------------------------------------
    log_level: str = "INFO"
    enable_docs: bool = True
    port: int = 8000
    app_version: str = Field(default="1.0.0")

    # ---------------------------------------------------------------------
    @field_validator("linkedin_jsessionid")
    @classmethod
    def _strip_quotes(cls, v: str) -> str:
        """JSESSIONID is stored quoted in the browser; LinkedIn wants it raw."""
        return v.strip().strip('"').strip("'")

    @field_validator("linkedin_li_at", "linkedin_bcookie", "linkedin_lidc")
    @classmethod
    def _strip_ws(cls, v: str) -> str:
        return v.strip().strip('"').strip("'")

    # ---------------------------------------------------------------------
    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def cors_origin_list(self) -> list[str]:
        raw = self.cors_origins.strip()
        if raw == "*":
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def effective_user_agent(self) -> str:
        return self.user_agent.strip() or DEFAULT_USER_AGENT

    @property
    def has_linkedin_session(self) -> bool:
        return bool(self.linkedin_li_at)

    @property
    def auth_required(self) -> bool:
        return bool(self.api_key_set)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
