"""Alembic wiring for the app.

Schema is owned by the versioned migrations in ``backend/alembic/versions/`` —
nothing in normal startup calls ``Base.metadata.create_all`` any more. This module
is the small bridge between the running app and Alembic:

* :func:`check_current` — fail fast at startup with an actionable message if the
  database is behind (or was never stamped).
* :func:`upgrade`, :func:`downgrade`, :func:`stamp`, :func:`current_revision` —
  thin wrappers the CLI and the tests use.
* :func:`adopt` — bring a pre-Alembic ("Hirelight-era") database under Alembic
  control without dropping any data.

CLI::

    python -m app.db_migrations current      # what revision is the DB at
    python -m app.db_migrations upgrade       # -> head (this is what run.sh calls)
    python -m app.db_migrations downgrade -1  # step back one revision
    python -m app.db_migrations stamp head    # mark DB as a revision, run no SQL
    python -m app.db_migrations adopt         # onboard a pre-Alembic database
    python -m app.db_migrations check         # exit non-zero if not at head
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.engine import Engine

from .config import get_settings

log = logging.getLogger("meridian.migrations")

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_ALEMBIC_INI = _BACKEND_DIR / "alembic.ini"


class MigrationsPending(RuntimeError):
    """The database schema is behind the code. Raised at startup so the process
    doesn't come up half-working against a stale schema."""


def alembic_config(url: str | None = None) -> Config:
    """An Alembic ``Config`` pointed at this project's ini/scripts, with the URL
    resolved (falling back to ``DB_URL``). Safe to call from any working dir."""
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("script_location", str(_BACKEND_DIR / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url or get_settings().db_url)
    return cfg


def head_revision(cfg: Config | None = None) -> str | None:
    script = ScriptDirectory.from_config(cfg or alembic_config())
    return script.get_current_head()


def current_revision(engine: Engine) -> str | None:
    # MigrationContext.configure logs two INFO lines about the dialect; not worth
    # surfacing every time the server checks its schema on boot.
    mig_log = logging.getLogger("alembic.runtime.migration")
    prior = mig_log.level
    mig_log.setLevel(logging.WARNING)
    try:
        with engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()
    finally:
        mig_log.setLevel(prior)


def _has_any_tables(engine: Engine) -> bool:
    names = set(inspect(engine).get_table_names())
    names.discard("alembic_version")
    return bool(names)


def check_current(engine: Engine, *, cfg: Config | None = None) -> None:
    """Raise :class:`MigrationsPending` unless the database is at head."""
    cfg = cfg or alembic_config(str(engine.url))
    head = head_revision(cfg)
    current = current_revision(engine)
    if current == head:
        return

    if current is None and _has_any_tables(engine):
        raise MigrationsPending(
            "This database predates Alembic and has not been adopted yet. "
            "Back it up, then run:  python -m app.db_migrations adopt"
        )
    raise MigrationsPending(
        f"Database is at revision {current or '(empty)'}, code expects {head}. "
        "Run:  python -m app.db_migrations upgrade"
    )


def upgrade(revision: str = "head", url: str | None = None) -> None:
    command.upgrade(alembic_config(url), revision)


def downgrade(revision: str, url: str | None = None) -> None:
    command.downgrade(alembic_config(url), revision)


def stamp(revision: str = "head", url: str | None = None) -> None:
    command.stamp(alembic_config(url), revision)


def adopt(url: str | None = None) -> None:
    """Bring an existing database under Alembic control without data loss.

    * fresh / empty database -> just ``upgrade head``
    * already stamped        -> ``upgrade head`` (idempotent)
    * pre-Alembic database   -> run the one-shot legacy catch-up (adds any columns
      a very old single-workspace DB is missing and folds its global résumé /
      threshold / jobs into a "Default" workspace), then ``stamp head``
    """
    settings_url = url or get_settings().db_url
    engine = create_engine(settings_url)
    try:
        if current_revision(engine) is not None:
            log.info("Database already under Alembic control; upgrading to head.")
            upgrade(url=settings_url)
            return
        if not _has_any_tables(engine):
            log.info("Empty database; creating schema at head.")
            upgrade(url=settings_url)
            return

        log.info("Pre-Alembic database detected; running one-time legacy catch-up.")
        from .migrations import run_migrations

        run_migrations(engine)
        stamp(url=settings_url)
        log.info("Legacy database adopted and stamped at head.")
    finally:
        engine.dispose()

    post = create_engine(settings_url)
    try:
        check_current(post)
        log.info("Adoption verified: schema matches head with no drift.")
    finally:
        post.dispose()


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.db_migrations")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("current")
    sub.add_parser("check")
    sub.add_parser("adopt")
    up = sub.add_parser("upgrade")
    up.add_argument("revision", nargs="?", default="head")
    down = sub.add_parser("downgrade")
    down.add_argument("revision")
    st = sub.add_parser("stamp")
    st.add_argument("revision", nargs="?", default="head")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    url = get_settings().db_url

    if args.cmd == "current":
        engine = create_engine(url)
        print(current_revision(engine) or "(none — database not stamped)")
        engine.dispose()
    elif args.cmd == "check":
        engine = create_engine(url)
        try:
            check_current(engine)
        except MigrationsPending as exc:
            print(f"NOT up to date: {exc}", file=sys.stderr)
            return 1
        finally:
            engine.dispose()
        print("Database is at head.")
    elif args.cmd == "upgrade":
        engine = create_engine(url)
        try:
            if current_revision(engine) is None and _has_any_tables(engine):
                print(
                    "This database predates Alembic and cannot be upgraded directly.\n"
                    "Back it up, then run:  python -m app.db_migrations adopt",
                    file=sys.stderr,
                )
                return 1
        finally:
            engine.dispose()
        upgrade(args.revision, url)
    elif args.cmd == "downgrade":
        downgrade(args.revision, url)
    elif args.cmd == "stamp":
        stamp(args.revision, url)
    elif args.cmd == "adopt":
        adopt(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
