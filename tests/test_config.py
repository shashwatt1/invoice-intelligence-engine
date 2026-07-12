"""
tests/test_config.py — Configuration system tests.

Verifies that:
- Settings loads without errors.
- Computed properties return correct values.
- MIME type and origin list parsing works for comma-separated env vars.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings


def test_settings_loads_defaults() -> None:
    """Settings must load with defaults when no .env overrides are present."""
    settings = get_settings()
    assert settings.app_name == "Invoice Intelligence Platform"
    assert settings.app_version == "0.1.0"
    assert settings.max_upload_size_mb == 25


def test_max_upload_size_bytes_property() -> None:
    """max_upload_size_bytes must equal max_upload_size_mb * 1024 * 1024."""
    settings = get_settings()
    assert settings.max_upload_size_bytes == settings.max_upload_size_mb * 1024 * 1024


def test_allowed_mime_types_is_list() -> None:
    """allowed_mime_types must be a list, not a raw string."""
    settings = get_settings()
    assert isinstance(settings.allowed_mime_types, list)
    assert "application/pdf" in settings.allowed_mime_types


def test_parse_mime_types_from_string() -> None:
    """Comma-separated MIME types in env var must be parsed into a list."""
    s = Settings(
        _env_file=None,
        allowed_mime_types="application/pdf,image/png,image/jpeg",
    )
    assert s.allowed_mime_types == ["application/pdf", "image/png", "image/jpeg"]


def test_comma_separated_lists_load_from_dotenv_file(tmp_path) -> None:
    """
    Regression: pydantic-settings JSON-decodes complex fields from .env
    files BEFORE field validators run. Without NoDecode on the list
    fields, a template-style .env (comma-separated values) crashed the
    app at startup with a SettingsError.
    """
    env_file = tmp_path / ".env"
    env_file.write_text(
        "ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8080\n"
        "ALLOWED_MIME_TYPES=application/pdf,image/png\n"
    )
    s = Settings(_env_file=str(env_file))
    assert s.allowed_origins == ["http://localhost:3000", "http://localhost:8080"]
    assert s.allowed_mime_types == ["application/pdf", "image/png"]


def test_is_development_flag() -> None:
    """is_development must be True when app_env=development."""
    s = Settings(_env_file=None, app_env="development")
    assert s.is_development is True
    assert s.is_production is False


def test_is_production_flag() -> None:
    """is_production must be True when app_env=production."""
    s = Settings(_env_file=None, app_env="production")
    assert s.is_production is True
    assert s.is_development is False
