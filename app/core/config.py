"""
Configuration System — app/core/config.py

Loads all environment variables using Pydantic Settings.
A single `Settings` instance is created at module import time and
shared across the entire application via the `get_settings()` dependency.

Design decisions:
- Pydantic Settings validates types at startup — bad config fails fast.
- All secrets come from environment variables, never from source code.
- Settings is a singleton (cached via @lru_cache) so .env is parsed once.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Central configuration object for the Invoice Intelligence Platform.

    All fields are loaded from environment variables (or .env file).
    Field names map directly to environment variable names (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",  # Silently ignore unknown env vars
    )

    # -------------------------------------------------------------------------
    # Application
    # -------------------------------------------------------------------------
    app_env: Literal["development", "staging", "production"] = "development"
    app_name: str = "Invoice Intelligence Platform"
    app_version: str = "0.1.0"
    debug: bool = False

    # -------------------------------------------------------------------------
    # API Server
    # -------------------------------------------------------------------------
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    api_prefix: str = "/api/v1"
    allowed_origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"]
    )

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def parse_origins(cls, v: str | list[str]) -> list[str]:
        """Allow ALLOWED_ORIGINS to be set as a comma-separated string."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # -------------------------------------------------------------------------
    # Database
    # -------------------------------------------------------------------------
    database_url: str = Field(
        default="postgresql+asyncpg://invoice_user:invoice_pass@localhost:5432/invoice_db",
        description="Async SQLAlchemy connection URL (asyncpg driver).",
    )
    database_url_sync: str = Field(
        default="postgresql+psycopg2://invoice_user:invoice_pass@localhost:5432/invoice_db",
        description="Sync connection URL used by Alembic migrations.",
    )
    database_pool_size: int = 10
    database_max_overflow: int = 20
    database_pool_timeout: int = 30
    database_echo: bool = False  # Log SQL — enable only for debugging

    # -------------------------------------------------------------------------
    # File Storage
    # -------------------------------------------------------------------------
    storage_backend: Literal["local", "s3", "supabase"] = "local"
    upload_path: str = "./uploads"
    max_upload_size_mb: int = 25
    allowed_mime_types: list[str] = Field(
        default=["application/pdf", "image/png", "image/jpeg"]
    )

    @field_validator("allowed_mime_types", mode="before")
    @classmethod
    def parse_mime_types(cls, v: str | list[str]) -> list[str]:
        """Allow ALLOWED_MIME_TYPES to be set as a comma-separated string."""
        if isinstance(v, str):
            return [m.strip() for m in v.split(",") if m.strip()]
        return v

    @property
    def max_upload_size_bytes(self) -> int:
        """Computed max upload size in bytes for use in upload validation."""
        return self.max_upload_size_mb * 1024 * 1024

    # S3 settings
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    s3_bucket_name: str = ""

    # Supabase settings
    supabase_url: str = ""
    supabase_service_key: str = ""
    supabase_bucket_name: str = ""

    # -------------------------------------------------------------------------
    # OCR (used from Sprint 2 onward)
    # -------------------------------------------------------------------------
    ocr_provider: Literal["google_vision", "paddleocr", "easyocr"] = "google_vision"
    google_vision_api_key: str = ""

    # -------------------------------------------------------------------------
    # AI Structuring
    # -------------------------------------------------------------------------
    llm_provider: Literal["openai"] = "openai"
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_max_retries: int = 3
    openai_timeout_seconds: int = 30
    openai_max_tokens_per_document: int = 6000

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------
    validation_rounding_tolerance: float = 0.02
    review_confidence_threshold: float = 0.85

    # -------------------------------------------------------------------------
    # Logging
    # -------------------------------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    log_file_enabled: bool = True
    log_file_path: str = "./logs/app.log"

    # -------------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------------
    secret_key: str = "change-me-in-production"
    api_key_header: str = "X-API-Key"
    access_token_expire_minutes: int = 60

    @property
    def is_production(self) -> bool:
        """Convenience check for production-specific guards."""
        return self.app_env == "production"

    @property
    def is_development(self) -> bool:
        """Convenience check for development-mode features."""
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Return the singleton Settings instance.

    Uses lru_cache so the .env file is read exactly once per process.
    In tests, call get_settings.cache_clear() before patching env vars.
    """
    return Settings()
