"""Lightweight in-app migration: this project has no Alembic, so on startup we
create any new tables and, if an older single-workspace database is detected,
fold its existing global resume/threshold/jobs into an auto-created "Default"
workspace so nothing already saved is lost.
"""

import logging

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from .config import get_settings
from .db import Base

log = logging.getLogger("meridian.migrations")


def run_migrations(engine: Engine) -> None:
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "jobs" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("jobs")}
    had_external_id_index = any(
        idx["name"] == "ix_jobs_external_id" for idx in inspector.get_indexes("jobs")
    )
    settings = get_settings()

    with engine.begin() as conn:
        for column, ddl_type in (
            ("workspace_id", "INTEGER"),
            ("auto_apply_state", "VARCHAR(30)"),
            ("review_reason", "VARCHAR(50)"),
            ("last_batch_run_id", "INTEGER"),
            ("source_batch_id", "INTEGER"),
        ):
            if column not in columns:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {column} {ddl_type}"))

        if "batches" in inspector.get_table_names():
            batch_columns = {col["name"] for col in inspector.get_columns("batches")}
            if "source" not in batch_columns:
                conn.execute(
                    text("ALTER TABLE batches ADD COLUMN source VARCHAR(10) DEFAULT 'search'")
                )

        orphaned = conn.execute(
            text("SELECT COUNT(*) FROM jobs WHERE workspace_id IS NULL")
        ).scalar()
        if orphaned:
            legacy_resume = conn.execute(
                text("SELECT value FROM settings WHERE key = 'resume'")
            ).scalar()
            legacy_threshold = conn.execute(
                text("SELECT value FROM settings WHERE key = 'threshold'")
            ).scalar()
            default_id = conn.execute(
                text("SELECT id FROM workspaces WHERE name = 'Default' ORDER BY id LIMIT 1")
            ).scalar()
            if not default_id:
                result = conn.execute(
                    text(
                        "INSERT INTO workspaces "
                        "(name, resume_text, threshold, auto_apply_threshold, "
                        "profile_name, profile_email, profile_phone, profile_linkedin, "
                        "cover_letter, created_at, updated_at) "
                        "VALUES (:name, :resume, :threshold, :auto_threshold, "
                        "'', '', '', '', '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ),
                    {
                        "name": "Default",
                        "resume": legacy_resume or "",
                        "threshold": float(legacy_threshold)
                        if legacy_threshold
                        else settings.score_threshold,
                        "auto_threshold": settings.auto_apply_threshold,
                    },
                )
                default_id = result.lastrowid
            conn.execute(
                text("UPDATE jobs SET workspace_id = :wid WHERE workspace_id IS NULL"),
                {"wid": default_id},
            )
            log.info("Migrated %s legacy job(s) into workspace #%s (Default)", orphaned, default_id)

        # Jobs used to be unique on a bare external_id; now that they're scoped per
        # workspace, uniqueness has to be (workspace_id, external_id) instead.
        if had_external_id_index:
            conn.execute(text("DROP INDEX IF EXISTS ix_jobs_external_id"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_workspace_external "
                "ON jobs (workspace_id, external_id)"
            )
        )
