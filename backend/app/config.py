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

    # Multi-agent coverage analysis. Each role (A analyst, B challenger, C judge)
    # maps to an OpenAI compatible endpoint serving an open-weight model inside
    # the bank's environment (see docs/MODELS.md). With "auto" the models are
    # used when all three endpoints are configured; otherwise the deterministic
    # rule based implementation runs, and any role that fails at run time falls
    # back to it and is recorded as such (never presented as a model result).
    analysis_mode: Literal["auto", "models", "deterministic"] = "auto"
    model_a_url: str | None = None
    model_a_name: str = "Qwen/Qwen3-32B"
    model_a_api_key: str | None = None
    model_b_url: str | None = None
    model_b_name: str = "mistralai/Mistral-Small-3.2-24B-Instruct-2506"
    model_b_api_key: str | None = None
    model_c_url: str | None = None
    model_c_name: str = "zai-org/GLM-4.5-Air"
    model_c_api_key: str | None = None
    model_timeout_seconds: float = 90.0
    model_max_retries: int = 1
    model_max_tokens: int = 3000
    analysis_run_in_background: bool = True

    # Account security
    lockout_threshold: int = 5
    lockout_minutes: int = 15
    password_reset_ttl_minutes: int = 30
    mfa_required_for_privileged: bool = False  # set true in production once staff enrolled

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
    def models_configured(self) -> bool:
        return bool(self.model_a_url and self.model_b_url and self.model_c_url)

    @property
    def effective_analysis_mode(self) -> str:
        if self.analysis_mode == "auto":
            return "models" if self.models_configured else "deterministic"
        return self.analysis_mode

    @property
    def is_dev(self) -> bool:
        return self.app_env != "production"


settings = Settings()
