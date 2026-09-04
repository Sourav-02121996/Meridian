"""Alembic environment for Meridian.

The database URL is not stored in alembic.ini — it comes from the same place the
app gets it (``DB_URL`` in ``backend/.env``, via ``app.config.get_settings``) so
there is only ever one source of truth. A caller can still override it:

* CLI:  ``alembic -x db_url=sqlite:///./other.db upgrade head``
* code: ``cfg.set_main_option("sqlalchemy.url", ...)`` before invoking a command
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Importing the models module registers every table on Base.metadata, which is
# what --autogenerate diffs the live database against.
from app.db import Base
from app import models  # noqa: F401  (imported for the side effect above)

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers=False: this env runs in-process from the app and the
    # test suite too (via app.db_migrations), and the default True would silently
    # switch off every logger configured before Alembic was invoked.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _resolve_url() -> str:
    """`-x db_url=...` wins, then an explicitly set sqlalchemy.url, then app config."""
    x_args = context.get_x_argument(as_dictionary=True)
    if x_args.get("db_url"):
        return x_args["db_url"]
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    from app.config import get_settings

    return get_settings().db_url


def _configure_kwargs(url: str) -> dict:
    # render_as_batch: SQLite has almost no native ALTER TABLE, so Alembic has to
    # rebuild-and-copy tables for most column changes; batch mode does that.
    # compare_type: catch column type changes in --autogenerate too.
    return {
        "target_metadata": target_metadata,
        "compare_type": True,
        "render_as_batch": url.startswith("sqlite"),
    }


def run_migrations_offline() -> None:
    url = _resolve_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        **_configure_kwargs(url),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url = _resolve_url()
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url
    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, **_configure_kwargs(url))
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
