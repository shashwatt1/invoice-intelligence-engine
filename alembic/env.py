"""
Alembic Environment — alembic/env.py

Configures Alembic to:
- Use the sync DATABASE_URL_SYNC from app settings (Alembic CLI is synchronous).
- Import all ORM models via app.database.base.Base so autogenerate detects them.
- Run migrations in online mode (against a live database).

Design decisions:
- Alembic is always driven by the same model metadata as the application,
  preventing schema drift between code and database.
- DATABASE_URL_SYNC is the psycopg2 (sync) URL because Alembic's run_migrations
  is a synchronous operation.
- We import all model modules here so their Table definitions are registered
  on Base.metadata before autogenerate scans it.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# ---------------------------------------------------------------------------
# Import application metadata
# ---------------------------------------------------------------------------
# This import registers Base.metadata with all mapped tables.
# When new model files are added, import them here so autogenerate sees them.
from app.core.config import get_settings
from app.database.base import Base

# Future Sprint 2+ model imports (uncomment as models are added):
# from app.models.invoice import Invoice          # noqa: F401
# from app.models.vendor import Vendor            # noqa: F401
# from app.models.invoice_item import InvoiceItem # noqa: F401
# from app.models.user import User                # noqa: F401
# from app.models.audit_log import AuditLog       # noqa: F401

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to alembic.ini values
# ---------------------------------------------------------------------------
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override sqlalchemy.url with the value from our Settings object
settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url_sync)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    Generates SQL scripts without connecting to a database.
    Useful for reviewing migration SQL before applying to production.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Run migrations in 'online' mode — connects to the database.

    Used for all normal migration operations (upgrade, downgrade).
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,       # Detect column type changes
            compare_server_default=True,  # Detect default value changes
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
