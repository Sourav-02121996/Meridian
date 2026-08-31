"""Regression test for the cross-workspace stale-job bug: a job left over from a
deleted batch that happens to share its numeric id with a brand-new, unrelated
batch (batches.id has no AUTOINCREMENT, so ids get reused) must never be swept
into that new batch's run just because source_batch_id matches — it belongs to a
different workspace entirely. Uses a real in-memory DB (same pattern as
test_qa_bank.py's _memory_session) rather than mocking the query itself, so this
actually exercises _run_batch's real SQL, not a hand-wave.
"""

from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.scheduler as scheduler_module
from app.apply_adapters import AutoApplyResult
from app.db import Base
from app.models import Batch, BatchSource, BatchStatus, Job, JobStatus, Workspace


def _memory_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def test_run_batch_never_touches_a_job_from_a_different_workspace(monkeypatch):
    Session = _memory_sessionmaker()
    db = Session()

    real_workspace = Workspace(name="Real", resume_file=b"%PDF fake", resume_filename="r.pdf")
    stale_workspace = Workspace(name="Stale leftover", resume_file=None)
    db.add_all([real_workspace, stale_workspace])
    db.commit()

    batch = Batch(
        workspace_id=real_workspace.id,
        source=BatchSource.upload,
        status=BatchStatus.active,
        auto_apply_threshold=0,
        start_at=datetime.now(timezone.utc),
    )
    db.add(batch)
    db.commit()

    # The legitimate job: belongs to the batch's own workspace.
    own_job = Job(
        workspace_id=real_workspace.id,
        external_id="own",
        apply_url="https://example.com/own",
        ats_platform="greenhouse",
        score=100,
        status=JobStatus.discovered,
        source_batch_id=batch.id,
    )
    # The stale job: a leftover from some earlier, now-deleted batch that used to
    # hold this same id — a different workspace entirely, coincidentally sharing
    # source_batch_id with the new batch above.
    stale_job = Job(
        workspace_id=stale_workspace.id,
        external_id="stale",
        apply_url="https://example.com/stale",
        ats_platform="greenhouse",
        score=100,
        status=JobStatus.discovered,
        source_batch_id=batch.id,
    )
    db.add_all([own_job, stale_job])
    db.commit()
    db.close()

    with (
        patch.object(scheduler_module, "SessionLocal", Session),
        patch.object(
            scheduler_module, "attempt_apply", return_value=AutoApplyResult(True)
        ) as mock_attempt_apply,
    ):
        scheduler_module._run_batch(batch.id)

    # Only the real workspace's own job should ever have been handed to attempt_apply.
    assert mock_attempt_apply.call_count == 1
    assert mock_attempt_apply.call_args[0][0] == "https://example.com/own"

    verify_db = Session()
    refreshed_own = verify_db.get(Job, own_job.id)
    refreshed_stale = verify_db.get(Job, stale_job.id)
    assert refreshed_own.auto_apply_state == "applied_auto"
    assert refreshed_own.status == JobStatus.applied
    # The stale, other-workspace job must be completely untouched.
    assert refreshed_stale.auto_apply_state is None
    assert refreshed_stale.status == JobStatus.discovered


def test_run_batch_reconsiders_a_job_a_previous_run_already_auto_applied_to(monkeypatch):
    """A successful auto-apply sets status=applied (asserted above, and relied on
    by stats.py/export.py for accurate "applied" counts) -- but that must never by
    itself make a later batch treat the job as already "actioned by the user" and
    skip it forever. Only a genuine manual status change (which always clears
    auto_apply_state back to None -- see routes/jobs.py::patch_job) should do
    that. Here the job is already status=applied/auto_apply_state=applied_auto
    from an earlier run, with no manual action since; a fresh batch touching the
    exact same row must still hand it to attempt_apply again."""
    Session = _memory_sessionmaker()
    db = Session()

    workspace = Workspace(name="Real", resume_file=b"%PDF fake", resume_filename="r.pdf")
    db.add(workspace)
    db.commit()

    batch = Batch(
        workspace_id=workspace.id,
        source=BatchSource.upload,
        status=BatchStatus.active,
        auto_apply_threshold=0,
        start_at=datetime.now(timezone.utc),
    )
    db.add(batch)
    db.commit()

    already_auto_applied = Job(
        workspace_id=workspace.id,
        external_id="already-applied",
        apply_url="https://example.com/already-applied",
        ats_platform="greenhouse",
        score=100,
        status=JobStatus.applied,
        auto_apply_state="applied_auto",
        source_batch_id=batch.id,
    )
    manually_marked_applied = Job(
        workspace_id=workspace.id,
        external_id="manually-applied",
        apply_url="https://example.com/manually-applied",
        ats_platform="greenhouse",
        score=100,
        status=JobStatus.applied,
        auto_apply_state=None,
        source_batch_id=batch.id,
    )
    db.add_all([already_auto_applied, manually_marked_applied])
    db.commit()
    db.close()

    with (
        patch.object(scheduler_module, "SessionLocal", Session),
        patch.object(
            scheduler_module, "attempt_apply", return_value=AutoApplyResult(True)
        ) as mock_attempt_apply,
    ):
        scheduler_module._run_batch(batch.id)

    # Only the auto-applied job (never explicitly actioned by a human) is retried;
    # the manually-marked one is left alone exactly as before.
    assert mock_attempt_apply.call_count == 1
    assert mock_attempt_apply.call_args[0][0] == "https://example.com/already-applied"
