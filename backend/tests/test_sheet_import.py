"""Regression coverage for upload batches always re-applying to every row they're
given, regardless of what a re-uploaded (previously exported) sheet's own Status/
Date Applied columns say, and regardless of whatever the existing DB row's status
already was -- see sheet_import.py's module docstring and upsert_jobs_from_rows.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Job, JobStatus, Workspace
from app.sheet_import import upsert_jobs_from_rows


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def test_upload_ignores_an_applied_status_from_the_sheet_itself():
    """A row whose own Status cell says "Applied" (e.g. a previous export,
    re-uploaded verbatim) must still come in as discovered -- the Status column
    is tolerated, never honored, for a brand-new row."""
    db = _memory_session()
    workspace = Workspace(name="W")
    db.add(workspace)
    db.commit()

    rows = [
        {
            "apply_url": "https://example.com/job",
            "title": "Engineer",
            "company": "Acme",
            "status": "applied",
            "date_applied": "2026-01-01",
        }
    ]
    upsert_jobs_from_rows(db, rows, workspace.id, batch_id=1)
    db.commit()

    job = db.query(Job).one()
    assert job.status == JobStatus.discovered
    assert job.date_applied is None


def test_reupload_resets_an_already_applied_existing_row_back_to_discovered():
    """The same URL already exists in the DB, already marked applied (auto or
    manual) by an earlier run -- re-uploading it must still force it back to
    discovered, since uploading a specific URL is itself the explicit per-run
    instruction to (re)attempt it."""
    db = _memory_session()
    workspace = Workspace(name="W")
    db.add(workspace)
    db.commit()

    existing = Job(
        workspace_id=workspace.id,
        external_id="import:placeholder",
        apply_url="https://example.com/job",
        title="Engineer",
        company="Acme",
        ats_platform="greenhouse",
        status=JobStatus.applied,
        auto_apply_state="applied_auto",
    )
    db.add(existing)
    db.commit()
    # Match the real external_id derivation so this upsert actually finds the
    # existing row instead of creating a second one for the same URL.
    from app.sheet_import import import_external_id

    existing.external_id = import_external_id("https://example.com/job")
    db.commit()

    rows = [{"apply_url": "https://example.com/job", "title": "Engineer", "company": "Acme"}]
    upsert_jobs_from_rows(db, rows, workspace.id, batch_id=2)
    db.commit()

    job = db.query(Job).one()
    assert job.status == JobStatus.discovered
    assert job.auto_apply_state is None
    assert job.date_applied is None
