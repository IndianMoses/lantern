"""Application settings.

All secrets and environment specific values come from environment variables
(or a local .env file in development). Nothing sensitive is hard coded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Lantern"
    app_env: Literal["development", "test", "production"] = "development"
    secret_key: str = Field(default="dev-only-change-me", description="Used to sign session and approval tokens")

    # Database. SQLite by default so the project runs with one command.
    # Any SQLAlchemy URL works, e.g. postgresql+psycopg://user:pass@host/db
    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'lantern.db'}"

    # Storage for uploaded documents (private, never served directly)
    upload_dir: Path = REPO_ROOT / "data" / "uploads"
    operation_log_dir: Path = REPO_ROOT / "logs" / "operations"
    max_upload_bytes: int = 10 * 1024 * 1024

    # LLM provider: "anthropic" uses the Claude API; "deterministic" is a fully
    # offline rule based planner used for tests, sandbox evidence and demos
    # without credentials. "auto" picks anthropic when a key is present.
    llm_provider: Literal["auto", "anthropic", "deterministic"] = "auto"
    anthropic_api_key: str | None = None
    llm_model: str = "claude-sonnet-5-5"
    llm_max_tokens: int = 1800
    llm_timeout_seconds: float = 60.0

    # Sessions and approvals
    session_ttl_hours: int = 12
    approval_ttl_minutes: int = 15
    cookie_secure: bool = False  # set true behind HTTPS in production
    cookie_name: str = "lantern_session"

    # Rate limits (requests per minute)
    rate_limit_assistant_per_minute: int = 20
    rate_limit_login_per_minute: int = 10

    # Mock insurer adapter behaviour
    mock_adapter_fail_keyword: str = "SIMULATE_ADAPTER_FAILURE"

    # Development only: expose demo account list at /api/dev/demo-accounts
    expose_demo_accounts: bool = True

    @property
    def effective_llm_provider(self) -> str:
        if self.llm_provider == "auto":
            return "anthropic" if self.anthropic_api_key else "deterministic"
        return self.llm_provider

    @property
    def is_dev(self) -> bool:
        return self.app_env != "production"


settings = Settings()
