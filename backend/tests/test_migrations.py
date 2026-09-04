"""Alembic migration coverage.

These tests never touch the real ``meridian.db`` — every case runs against a
throwaway sqlite file under ``tmp_path`` with its URL passed explicitly.
"""

from sqlalchemy import create_engine, inspect

from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext

from app import db_migrations
from app.db import Base
from app.db_migrations import (
    MigrationsPending,
    adopt,
    alembic_config,
    check_current,
    current_revision,
    head_revision,
    stamp,
    upgrade,
)
from app.models import Job, JobStatus, Setting, Workspace


def _url(tmp_path, name="m.db"):
    return f"sqlite:///{tmp_path / name}"


def _schema_diff(engine):
    """Autogenerate ops needed to turn `engine`'s live schema into the models —
    empty list means the database already matches the code exactly."""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"compare_type": True, "target_metadata": Base.metadata}
        )
        return compare_metadata(ctx, Base.metadata)


def test_baseline_reproduces_current_schema_on_a_clean_db(tmp_path):
    url = _url(tmp_path)
    upgrade("head", url)

    engine = create_engine(url)
    assert current_revision(engine) == head_revision()
    assert _schema_diff(engine) == []

    tables = set(inspect(engine).get_table_names())
    assert {"workspaces", "jobs", "batches", "batch_runs", "job_blocked_questions",
            "settings", "alembic_version"} <= tables


def test_stamped_baseline_upgrades_cleanly_to_head(tmp_path):
    """Stamp a database at the *earliest* revision (not head) with no DDL, then
    run upgrade — proves the chain from baseline to head applies cleanly on top
    of a database that already has the baseline schema, rather than only ever
    being exercised by running every migration from empty (which is a different
    Alembic code path and would not have caught a follow-up migration that
    redundantly tried to recreate something the baseline already made — a real
    bug hit once during development of this migration setup)."""
    from alembic.script import ScriptDirectory

    url = _url(tmp_path)
    engine = create_engine(url)

    # A database that already has the *baseline* schema, stamped at the
    # baseline revision specifically — not head, even though today they're the
    # same revision; this is what "someone adopted a while ago and hasn't
    # upgraded since" looks like once more revisions exist.
    Base.metadata.create_all(engine)
    (base_rev,) = ScriptDirectory.from_config(alembic_config(url)).get_bases()
    stamp(base_rev, url)
    assert current_revision(engine) == base_rev

    upgrade("head", url)

    after = create_engine(url)
    assert current_revision(after) == head_revision()
    assert _schema_diff(after) == []


def test_upgrade_then_downgrade_to_base_is_clean(tmp_path):
    url = _url(tmp_path)
    upgrade("head", url)
    db_migrations.downgrade("base", url)

    engine = create_engine(url)
    names = set(inspect(engine).get_table_names())
    # Only Alembic's own bookkeeping table remains after a full downgrade.
    assert names in ({"alembic_version"}, set())


def test_migrated_db_is_writable_end_to_end(tmp_path):
    url = _url(tmp_path)
    upgrade("head", url)

    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=create_engine(url))()
    ws = Workspace(name="W")
    session.add(ws)
    session.commit()
    session.add(Job(workspace_id=ws.id, external_id="x1", status=JobStatus.discovered))
    session.commit()
    assert session.query(Job).count() == 1


def test_check_current_flags_a_pending_upgrade(tmp_path):
    url = _url(tmp_path)
    upgrade("head", url)
    engine = create_engine(url)

    # Pretend the code moved on to a newer head than the DB is stamped at.
    real_head = head_revision
    db_migrations.head_revision = lambda cfg=None: "deadbeef"
    try:
        with_err = None
        try:
            check_current(engine)
        except MigrationsPending as exc:
            with_err = str(exc)
    finally:
        db_migrations.head_revision = real_head

    assert with_err is not None
    assert "python -m app.db_migrations upgrade" in with_err


def test_check_current_flags_an_unstamped_pre_alembic_db(tmp_path):
    url = _url(tmp_path)
    engine = create_engine(url)
    Base.metadata.create_all(engine)  # legacy create_all, no alembic_version

    try:
        check_current(engine)
        raised = None
    except MigrationsPending as exc:
        raised = str(exc)

    assert raised is not None
    assert "adopt" in raised


def test_adopt_onboards_a_pre_alembic_db_without_losing_data(tmp_path):
    url = _url(tmp_path)
    engine = create_engine(url)

    # Build a pre-Alembic-style database: schema via create_all, some real rows,
    # and a legacy global "settings" row, with no alembic_version table.
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker

    Session = sessionmaker(bind=engine)
    seed = Session()
    ws = Workspace(name="Existing")
    seed.add(ws)
    seed.commit()
    seed.add(Job(workspace_id=ws.id, external_id="keep-me", title="Staff Eng"))
    seed.add(Setting(key="threshold", value="80"))
    seed.commit()
    seed.close()
    engine.dispose()

    adopt(url)

    after = create_engine(url)
    assert current_revision(after) == head_revision()
    assert _schema_diff(after) == []

    check = sessionmaker(bind=after)()
    assert check.query(Job).filter_by(external_id="keep-me").one().title == "Staff Eng"
    assert check.query(Setting).filter_by(key="threshold").one().value == "80"


def test_adopt_is_idempotent(tmp_path):
    url = _url(tmp_path)
    upgrade("head", url)  # already a normal Alembic-managed DB
    adopt(url)            # must be a no-op, not an error
    adopt(url)

    engine = create_engine(url)
    assert current_revision(engine) == head_revision()


def test_alembic_config_defaults_to_app_db_url(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setattr(
        config, "get_settings", lambda: config.Settings(db_url="sqlite:///./from-env.db")
    )
    monkeypatch.setattr(db_migrations, "get_settings", config.get_settings)
    cfg = alembic_config()
    assert cfg.get_main_option("sqlalchemy.url") == "sqlite:///./from-env.db"


def test_stamp_sets_revision_without_running_ddl(tmp_path):
    url = _url(tmp_path)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    stamp("head", url)
    assert current_revision(create_engine(url)) == head_revision()
