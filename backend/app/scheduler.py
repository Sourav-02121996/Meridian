import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from .db import SessionLocal
from .discovery import discover_and_score
from .greenhouse_adapter import attempt_apply, is_greenhouse
from .models import (
    Batch,
    BatchRepeatMode,
    BatchRun,
    BatchSource,
    BatchStatus,
    Job,
    JobStatus,
    Workspace,
)

log = logging.getLogger("meridian.scheduler")
# SQLite drops tzinfo on round-trip, so every datetime read back out of the DB (start_at
# included) comes back naive-but-actually-UTC. Pinning the scheduler's own timezone to UTC
# makes it interpret those naive values correctly instead of assuming the machine's local
# timezone, which would silently shift every schedule by the local UTC offset.
scheduler = BackgroundScheduler(timezone=timezone.utc)

_INTERVAL_KWARGS = {"hour": {"hours": 1}, "day": {"days": 1}, "week": {"weeks": 1}}
_MISFIRE_GRACE_SECONDS = 300


def _is_missed_one_time(batch: Batch) -> bool:
    """A one-off batch whose start_at has already passed beyond the misfire grace
    window will never fire — APScheduler just drops it silently. Detect that case so
    we can mark it completed instead of leaving it stuck looking "active" forever."""
    if batch.interval_unit is not None:
        return False
    start_at = (
        batch.start_at if batch.start_at.tzinfo else batch.start_at.replace(tzinfo=timezone.utc)
    )
    return (datetime.now(timezone.utc) - start_at).total_seconds() > _MISFIRE_GRACE_SECONDS


def start() -> None:
    with SessionLocal() as db:
        for batch in db.scalars(select(Batch).where(Batch.status == BatchStatus.active)).all():
            if _is_missed_one_time(batch):
                batch.status = BatchStatus.completed
            else:
                _schedule(batch)
        db.commit()
    scheduler.start()


def shutdown() -> None:
    scheduler.shutdown(wait=False)


def _job_id(batch_id: int) -> str:
    return f"batch-{batch_id}"


def _schedule(batch: Batch) -> None:
    # Explicitly-constructed triggers don't inherit the scheduler's `timezone=`, so it has
    # to be passed here too — otherwise a naive start_at (see note above) gets read as the
    # machine's local time instead of UTC.
    if batch.interval_unit is None:
        trigger = DateTrigger(run_date=batch.start_at, timezone=timezone.utc)
    else:
        trigger = IntervalTrigger(
            start_date=batch.start_at,
            timezone=timezone.utc,
            **_INTERVAL_KWARGS[batch.interval_unit.value],
        )
    scheduler.add_job(
        run_batch,
        trigger=trigger,
        args=[batch.id],
        id=_job_id(batch.id),
        replace_existing=True,
        # If the backend was down (or busy) past the scheduled time, catch up within 5
        # minutes; beyond that, skip rather than surprise the user with a very late run.
        misfire_grace_time=_MISFIRE_GRACE_SECONDS,
    )


def schedule_batch(batch: Batch) -> None:
    """Call right after a batch is created, or resumed from paused."""
    _schedule(batch)


def next_run_at(batch_id: int) -> datetime | None:
    """The live next-fire time from APScheduler itself, rather than a DB column that
    would drift out of sync with the actual schedule."""
    job = scheduler.get_job(_job_id(batch_id))
    return job.next_run_time if job else None


def unschedule_batch(batch_id: int) -> None:
    try:
        scheduler.remove_job(_job_id(batch_id))
    except Exception:
        pass


def run_batch(batch_id: int) -> None:
    """Runs entirely in the scheduler's own worker thread — never on the request path.
    Only fires while the backend process is up (no external cron). Guarded end-to-end so
    a batch/run deleted out from under an in-flight execution can't crash the scheduler
    thread — it just logs and gives up on this run."""
    try:
        _run_batch(batch_id)
    except Exception:
        log.exception("Batch %s run bookkeeping failed", batch_id)


def _decide_and_apply(
    job: Job, workspace: Workspace, auto_apply_threshold: float, profile: dict, run_id: int
) -> bool:
    """The one decision both batch sources share: score vs. threshold, then Greenhouse
    or manual review. Mutates the job in place; returns True if it was auto-applied."""
    if job.score < auto_apply_threshold:
        job.auto_apply_state, job.review_reason = "needs_review", "below_threshold"
        applied = False
    elif not is_greenhouse(job.apply_url):
        job.auto_apply_state, job.review_reason = "needs_review", "unsupported_ats"
        applied = False
    elif not workspace.resume_file:
        job.auto_apply_state, job.review_reason = "needs_review", "no_resume_file"
        applied = False
    else:
        result = attempt_apply(
            job.apply_url, profile, workspace.resume_file, workspace.resume_filename
        )
        if result.success:
            job.auto_apply_state, job.review_reason = "applied_auto", None
            job.status = JobStatus.applied
            job.date_applied = datetime.now(timezone.utc)
            applied = True
        else:
            job.auto_apply_state, job.review_reason = "needs_review", result.reason
            applied = False
    job.last_batch_run_id = run_id
    return applied


def _run_batch(batch_id: int) -> None:
    with SessionLocal() as db:
        batch = db.get(Batch, batch_id)
        if not batch or batch.status != BatchStatus.active:
            return
        workspace = db.get(Workspace, batch.workspace_id)
        run = BatchRun(batch_id=batch.id)
        db.add(run)
        db.commit()
        try:
            if batch.source == BatchSource.upload:
                # Jobs were already imported (with their score) at batch-creation time;
                # this run just has to decide, not discover anything.
                touched = list(db.scalars(select(Job).where(Job.source_batch_id == batch.id)).all())
                counts = {"fetched": len(touched), "new": len(touched), "updated": 0}
            else:
                touched, counts = discover_and_score(
                    db, workspace, batch.query, batch.days, batch.max_jobs
                )
            profile = {
                "name": workspace.profile_name,
                "email": workspace.profile_email,
                "phone": workspace.profile_phone,
                "linkedin": workspace.profile_linkedin,
            }
            auto_applied = needs_review = 0
            for job in touched:
                if job.status != JobStatus.discovered:
                    continue  # don't touch jobs the user has already actioned themselves
                if _decide_and_apply(job, workspace, batch.auto_apply_threshold, profile, run.id):
                    auto_applied += 1
                else:
                    needs_review += 1
            run.fetched, run.new, run.updated = counts["fetched"], counts["new"], counts["updated"]
            run.auto_applied, run.needs_review, run.status = auto_applied, needs_review, "success"
        except Exception as exc:
            log.exception("Batch %s failed", batch.id)
            run.status, run.error = "failed", str(exc)
        run.finished_at = datetime.now(timezone.utc)
        batch.runs_completed += 1
        if (
            batch.interval_unit is None
            or batch.repeat_mode == BatchRepeatMode.count
            and batch.run_limit
            and batch.runs_completed >= batch.run_limit
        ):
            batch.status = BatchStatus.completed
            unschedule_batch(batch.id)
        db.commit()
