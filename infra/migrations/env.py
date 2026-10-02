"""Alembic environment using the same typed database configuration as the API."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Models now live in the shared declarative base, so autogenerate can compare
# against them. `packages.db.base` holds Base without importing the delivery
# layer, so this import cannot drag the API into the migration runtime.
from apps.api.db import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _register_models() -> None:
    """Import every model module so ``Base.metadata`` reflects the real schema.

    ``Base`` is only a registry. Importing the base class registers nothing, so
    ``--autogenerate`` was comparing an **empty** metadata against a populated
    database and proposing to drop all 36 tables. ``make revision m="..."`` runs
    exactly that command, so the trap was one keystroke away and produced a
    migration that would have destroyed the schema.

    Enumerated by walking ``services`` for ``*.models`` rather than hard-coding a
    list, so adding a service does not require remembering to update the migration
    environment as well -- which is exactly how the omission happened in the first
    place. A module that fails to import is a hard error: silently skipping it would
    reproduce this bug with a much worse failure mode.
    """
    import importlib
    import pkgutil

    import services

    registered = 0
    for module in pkgutil.walk_packages(services.__path__, "services."):
        if not module.name.endswith(".models"):
            continue
        importlib.import_module(module.name)
        registered += 1

    if registered == 0:
        raise RuntimeError(
            "No services.*.models modules were imported, so autogenerate would "
            "propose dropping every table. Refusing to run."
        )
    if not Base.metadata.tables:
        raise RuntimeError(
            "Base.metadata is empty after importing the model modules; autogenerate "
            "cannot compare anything and would emit a destructive migration."
        )


_register_models()

target_metadata = Base.metadata


def _database_url() -> str:
    """The database to migrate, from the environment or ``alembic.ini``.

    Read directly rather than through ``apps.api.config.Settings``. That class
    deliberately discards the whole environment unless
    ``ALLOW_LIVE_CREDENTIALS=1``, which is the right posture for a process
    serving HTTP and the wrong one for a migration: an operator who passes
    ``DATABASE_URL=... alembic upgrade head`` has named the target on purpose.
    Going through Settings meant the compose ``migrate`` service silently fell
    back to the built-in localhost default and failed to connect to the
    postgres it had just been told to use.

    The discrete ``DB_*`` parts are honoured too, so an operator who configured
    the deployment the way their provider documents it does not also have to
    hand-assemble a DSN for the one command that is most likely to be run
    against production.
    """
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url

    from urllib.parse import quote

    host = os.environ.get("DB_HOST", "").strip()
    if not host:
        return config.get_main_option("sqlalchemy.url")
    user = quote(os.environ.get("DB_USER", "agentpay"), safe="")
    password = quote(os.environ.get("DB_PASSWORD", "agentpay"), safe="")
    driver = os.environ.get("DB_DRIVER", "postgresql+psycopg")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME", "agentpay")
    return f"{driver}://{user}:{password}@{host}:{port}/{name}"


config.set_main_option("sqlalchemy.url", _database_url())


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
