"""Legacy one-shot catch-up for databases created before Alembic existed.

This is no longer run on startup — Alembic (``backend/alembic/``) owns the schema
now. It survives only as the first step of ``python -m app.db_migrations adopt``:
for a pre-Alembic database it creates any missing tables, adds the columns that
were bolted on incrementally over this project's early history, and folds an old
single-workspace database's global resume/threshold/jobs into an auto-created
"Default" workspace so nothing already saved is lost. ``adopt`` then stamps the
result at the Alembic baseline. Do not add new migrations here — create an
Alembic revision instead.
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
            ("last_apply_started_at", "DATETIME"),
            ("last_apply_finished_at", "DATETIME"),
            ("last_apply_detail", "TEXT"),
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
            for column, ddl_type in (
                ("job_title_query", "TEXT DEFAULT ''"),
                ("technology_keywords_query", "TEXT DEFAULT ''"),
                ("job_description_query", "TEXT DEFAULT ''"),
                ("departments", "TEXT DEFAULT '[]'"),
                ("seniority", "TEXT DEFAULT '[]'"),
            ):
                if column not in batch_columns:
                    conn.execute(text(f"ALTER TABLE batches ADD COLUMN {column} {ddl_type}"))

        if "workspaces" in inspector.get_table_names():
            workspace_columns = {col["name"] for col in inspector.get_columns("workspaces")}
            new_location_columns = {"profile_city", "profile_state", "profile_country"}
            for column in (
                "profile_portfolio_url",
                "profile_github_url",
                "profile_location",
                "profile_current_company",
                "profile_current_title",
                "profile_desired_salary",
                "profile_start_date",
                "profile_work_authorized",
                "profile_visa_sponsorship",
                "profile_willing_to_relocate",
                "profile_18_or_older",
                "profile_gender",
                "profile_race_ethnicity",
                "profile_veteran_status",
                "profile_disability_status",
                "profile_citizenship",
                "profile_security_clearance",
                "profile_background_check_consent",
                "profile_drug_test_consent",
                "profile_criminal_history",
                *new_location_columns,
            ):
                if column not in workspace_columns:
                    conn.execute(
                        text(f"ALTER TABLE workspaces ADD COLUMN {column} VARCHAR(300) DEFAULT ''")
                    )

            # One-time best-effort backfill: profile_location used to be the only
            # field (a single freeform string like "Boston,MA, USA"); city/state/
            # country are new and start blank on every existing workspace. Only
            # ever splits a location that looks unambiguously like "City, State,
            # Country" (exactly 3 comma-separated parts) — anything else is left
            # blank for the user to fill in themselves rather than guessed wrong.
            if not new_location_columns.issubset(workspace_columns):
                rows = conn.execute(
                    text(
                        "SELECT id, profile_location FROM workspaces "
                        "WHERE profile_location != '' "
                        "AND (profile_city = '' OR profile_city IS NULL)"
                    )
                ).fetchall()
                for row in rows:
                    parts = [p.strip() for p in row.profile_location.split(",")]
                    if len(parts) == 3 and all(parts):
                        conn.execute(
                            text(
                                "UPDATE workspaces SET profile_city = :city, "
                                "profile_state = :state, profile_country = :country "
                                "WHERE id = :id"
                            ),
                            {
                                "city": parts[0],
                                "state": parts[1],
                                "country": parts[2],
                                "id": row.id,
                            },
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

        # Jobs used to be *unique* on a bare external_id; now that they're scoped
        # per workspace, uniqueness has to be (workspace_id, external_id) instead —
        # but external_id on its own is still a plain (non-unique) lookup index on
        # the current model, so drop the old unique one and recreate it unique-less.
        if had_external_id_index:
            conn.execute(text("DROP INDEX IF EXISTS ix_jobs_external_id"))
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_job_workspace_external "
                "ON jobs (workspace_id, external_id)"
            )
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_jobs_external_id ON jobs (external_id)")
        )
