"""Alembic environment. The database URL comes from invoice_analyst.config unless a caller sets sqlalchemy.url
(the test suite does, to migrate its own throwaway database)."""

from logging.config import fileConfig

from sqlalchemy import create_engine, pool

from alembic import context
from invoice_analyst.config import settings
from invoice_analyst.db.models import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings().database_url


def _include_name(name, type_, parent_names) -> bool:
    # procrastinate's tables, types and functions are created from its own shipped SQL, not from our models
    return not (name or "").startswith("procrastinate_")


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"}, include_name=_include_name)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = create_engine(_url(), poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, include_name=_include_name,
                          compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
