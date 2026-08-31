import logging
from datetime import datetime, timezone

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select
from sqlalchemy.orm import Session

from .apply_adapters import (
    WORKDAY_UNSUPPORTED_REASON,
    AnswerAttempt,
    AnswerLookup,
    QuestionDescriptor,
    attempt_apply,
    canonicalize_country,
    is_workday,
)
from .apply_adapters.profile_similarity import match_profile_field
from .blocked_questions import record_blocked_questions
from .config import get_settings
from .db import SessionLocal
from .discovery import discover_and_score
from .llm_drafting import draft_educated_guess, draft_pending_drafts_for_job
from .models import (
    Batch,
    BatchRepeatMode,
    BatchRun,
    BatchSource,
    BatchStatus,
    Job,
    JobBlockedQuestion,
    JobStatus,
    Workspace,
)
from .resume_rag import draft_from_resume_rag

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


def _build_profile(workspace: Workspace) -> dict:
    """The full set of applicant-profile fields the Greenhouse adapter can attempt to
    fill. Anything left blank on the workspace is simply omitted from the form fill —
    never guessed or defaulted on the candidate's behalf."""
    return {
        "name": workspace.profile_name,
        "email": workspace.profile_email,
        "phone": workspace.profile_phone,
        "linkedin": workspace.profile_linkedin,
        "portfolio_url": workspace.profile_portfolio_url,
        "github_url": workspace.profile_github_url,
        "location": workspace.profile_location,
        "city": workspace.profile_city,
        "state": workspace.profile_state,
        "country": canonicalize_country(workspace.profile_country),
        "current_company": workspace.profile_current_company,
        "current_title": workspace.profile_current_title,
        "desired_salary": workspace.profile_desired_salary,
        "start_date": workspace.profile_start_date,
        "work_authorized": workspace.profile_work_authorized,
        "visa_sponsorship": workspace.profile_visa_sponsorship,
        "willing_to_relocate": workspace.profile_willing_to_relocate,
        "is_18_or_older": workspace.profile_18_or_older,
        "gender": workspace.profile_gender,
        "race_ethnicity": workspace.profile_race_ethnicity,
        "veteran_status": workspace.profile_veteran_status,
        "disability_status": workspace.profile_disability_status,
        "citizenship": workspace.profile_citizenship,
        "security_clearance": workspace.profile_security_clearance,
        "background_check_consent": workspace.profile_background_check_consent,
        "drug_test_consent": workspace.profile_drug_test_consent,
        "criminal_history": workspace.profile_criminal_history,
        "cover_letter": workspace.cover_letter,
    }


def _approved_answers_for_job(db: Session, job_id: int) -> dict[str, str]:
    """This exact job's own previously human-approved blocked-question answers,
    keyed by question text — the replacement for what used to happen implicitly
    through the now-retired workspace Q&A bank (an approved answer got promoted
    into the bank, which the next attempt's bank match would then find). Scoped to
    *this job only*, on purpose: an approved answer is deliberately not persisted
    anywhere else (see models.JobBlockedQuestion's docstring), so "Retry auto-apply"
    on this same job is the only place it's ever reused."""
    rows = db.scalars(
        select(JobBlockedQuestion).where(
            JobBlockedQuestion.job_id == job_id, JobBlockedQuestion.status == "approved"
        )
    ).all()
    return {row.question_text: row.answer_text for row in rows if row.answer_text}


def _make_answer_lookup(db: Session, job: Job, workspace: Workspace, profile: dict) -> AnswerLookup:
    """The full tier chain for a question fill_known_fields (Tier A) didn't
    resolve, tried in order until one produces an answer:

      0. This exact job's own previously human-approved answer, if this same
         question was already answered on a prior attempt (see
         _approved_answers_for_job) — a direct, explicit answer, not an inference.
      B. profile_similarity.match_profile_field — semantic match against the
         workspace's own profile fields (includes EEO/sensitive fields: see that
         module's docstring for why matching your own stated value is never a
         "guess").
      C. resume_rag.draft_from_resume_rag — grounded LLM draft over the résumé's
         most relevant retrieved excerpts, if `llm_answer_drafting_enabled`.
      D. llm_drafting.draft_educated_guess — permissive LLM draft for anything
         still unresolved (typically subjective/motivational questions, or a
         select/radio Tier B couldn't confidently pick), if
         `llm_educated_guess_enabled`. Passed this job's own title/company/
         description so "why do you want to join {company}" has something real to
         answer from — see llm_drafting.draft_educated_guess's docstring.

    Every LLM-backed tier already refuses EEO/sensitive questions on its own
    (is_eeo_question/is_sensitive_question) — this function doesn't duplicate that
    logic, just wires the tiers together in priority order."""
    approved = _approved_answers_for_job(db, job.id)
    settings = get_settings()

    def lookup(question: QuestionDescriptor) -> AnswerAttempt | None:
        approved_answer = approved.get(question.label)
        if approved_answer:
            return AnswerAttempt(value=approved_answer, source="human_approved")

        attempt = match_profile_field(question, profile)
        if attempt is not None:
            return attempt

        # A generated answer to a constrained choice is a factual selection, not
        # harmless prose. The Harvey failure demonstrated why this boundary is
        # necessary: without the posting's location in context, the model drafted
        # "Yes, I'm based in this location" for a Boston profile and a San
        # Francisco role. Capture these questions for explicit approval instead;
        # draft_pending_drafts_for_job may still offer an AI suggestion in the UI,
        # but it can never be submitted merely because the model produced it.
        if question.field_type in ("select", "radio", "checkbox"):
            return None

        if settings.llm_answer_drafting_enabled:
            attempt = draft_from_resume_rag(question, workspace.resume_text, profile)
            if attempt is not None:
                return attempt

        if settings.llm_educated_guess_enabled:
            guess = draft_educated_guess(
                question,
                workspace.resume_text,
                profile,
                job_title=job.title,
                job_company=job.company,
                job_description=job.description,
            )
            if guess is not None:
                return AnswerAttempt(value=guess, source="llm_guess")

        return None

    return lookup


def _decide_and_apply(
    job: Job,
    workspace: Workspace,
    auto_apply_threshold: float,
    profile: dict,
    run_id: int | None,
    db: Session,
) -> bool:
    """The one decision both batch sources share: score vs. threshold, then adapter
    lookup by ats_platform, or manual review. Mutates the job in place; returns True
    if it was auto-applied. `run_id` is None only for a manual retry_apply_job() call
    outside any batch run — there's no BatchRun row to link back to in that case, so
    job.last_batch_run_id is simply left as whatever it already was."""
    if job.score < auto_apply_threshold:
        job.auto_apply_state, job.review_reason = "needs_review", "below_threshold"
        applied = False
    elif is_workday(job.ats_platform):
        job.auto_apply_state, job.review_reason = "needs_review", WORKDAY_UNSUPPORTED_REASON
        applied = False
    elif not workspace.resume_file:
        job.auto_apply_state, job.review_reason = "needs_review", "no_resume_file"
        applied = False
    else:
        job.last_apply_started_at = datetime.now(timezone.utc)
        job.last_apply_finished_at = None
        job.last_apply_detail = None
        result = attempt_apply(
            job.apply_url,
            job.ats_platform,
            profile,
            workspace.resume_file,
            workspace.resume_filename,
            answer_lookup=_make_answer_lookup(db, job, workspace, profile),
        )
        if result.success:
            job.auto_apply_state, job.review_reason = "applied_auto", None
            job.status = JobStatus.applied
            job.date_applied = datetime.now(timezone.utc)
            job.last_apply_detail = result.detail
            applied = True
        else:
            job.auto_apply_state, job.review_reason = "needs_review", result.reason
            job.last_apply_detail = result.detail
            applied = False
            if result.reason == "custom_questions" and result.unresolved_questions:
                blocked_rows = record_blocked_questions(
                    db, job, workspace.id, result.unresolved_questions
                )
                if get_settings().llm_answer_drafting_enabled:
                    try:
                        draft_pending_drafts_for_job(db, workspace, blocked_rows)
                    except Exception:
                        log.exception(
                            "LLM drafting failed for job %s; leaving plain needs_review", job.id
                        )
        job.last_apply_finished_at = datetime.now(timezone.utc)
    if run_id is not None:
        job.last_batch_run_id = run_id
    return applied


def retry_apply_job(job_id: int) -> None:
    """Manually triggered from the Needs-Review UI's "Retry auto-apply" button —
    typically right after answering a blocked question, but usable for any
    needs_review reason. Runs the exact same real attempt_apply flow _decide_and_
    apply always has, for this one job, right now — an upload batch has
    run_limit=1, so without this the only way to give a freshly-answered question
    another shot was waiting for a new batch, which the fix in this same commit
    (scheduler._run_batch's workspace scoping) makes deliberately harder to
    conjure by accident. Same guarded top-level shape as run_batch: runs in a
    background task, never on the request path, and a job/workspace deleted out
    from under an in-flight retry just logs and gives up rather than crashing."""
    try:
        _retry_apply_job(job_id)
    except Exception as exc:
        log.exception("Retry-apply failed for job %s", job_id)
        # Never strand the UI in applying if the worker crashes outside the
        # adapter's own error boundary. Persist a terminal, diagnosable state in
        # a fresh session because the failing session may already be rolled back.
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            if job and job.auto_apply_state == "applying":
                job.auto_apply_state = "needs_review"
                job.review_reason = "unexpected_error"
                job.last_apply_finished_at = datetime.now(timezone.utc)
                job.last_apply_detail = f"Retry worker failed: {type(exc).__name__}: {exc}"[:4000]
                db.commit()


def _retry_apply_job(job_id: int) -> None:
    with SessionLocal() as db:
        job = db.get(Job, job_id)
        if not job or job.auto_apply_state not in ("needs_review", "applying"):
            return
        workspace = db.get(Workspace, job.workspace_id)
        if not workspace:
            return
        if job.auto_apply_state == "needs_review":
            job.auto_apply_state = "applying"
            job.review_reason = None
            job.last_apply_started_at = datetime.now(timezone.utc)
            job.last_apply_finished_at = None
            job.last_apply_detail = None
            db.commit()
        profile = _build_profile(workspace)
        # 0, not the batch's own original auto_apply_threshold: the user explicitly
        # asked for this one job to be retried right now, an opt-in strong enough
        # to skip re-litigating the score gate — the same job already cleared it
        # (or, if it didn't — below_threshold — this is the one deliberate way to
        # override that, exactly as intended by asking for a retry at all).
        _decide_and_apply(job, workspace, 0, profile, None, db)
        db.commit()


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
                # this run just has to decide, not discover anything. Also scoped to
                # this batch's own workspace, not just its id: batches.id has no
                # AUTOINCREMENT, so a deleted batch's id can be reused by a later,
                # unrelated one — without this, a stale job left over from a batch
                # that used to hold this same id (see routes/batches.py::delete_batch,
                # which now clears this FK on delete for the same reason) would get
                # silently swept into someone else's workspace's run and auto-applied
                # there, using that *other* workspace's résumé/profile. Confirmed
                # live: this is exactly how a job from an empty "Default" workspace
                # got auto-applied a second time under a completely different,
                # unrelated batch.
                touched = list(
                    db.scalars(
                        select(Job).where(
                            Job.source_batch_id == batch.id,
                            Job.workspace_id == batch.workspace_id,
                        )
                    ).all()
                )
                counts = {"fetched": len(touched), "new": len(touched), "updated": 0}
            else:
                touched, counts = discover_and_score(
                    db,
                    workspace,
                    batch.query,
                    batch.days,
                    batch.max_jobs,
                    departments=batch.departments or None,
                    seniority=batch.seniority or None,
                    job_title_query=batch.job_title_query,
                    technology_keywords_query=batch.technology_keywords_query,
                    job_description_query=batch.job_description_query,
                )
            profile = _build_profile(workspace)
            auto_applied = needs_review = 0
            for job in touched:
                # status != discovered alone isn't a safe "already actioned by the
                # user" signal on its own: a *successful auto-apply* also sets
                # status to applied (see _decide_and_apply's success branch, kept
                # that way so stats.py/export.py's "applied" counts stay accurate),
                # which used to make an auto-applied job silently invisible to
                # every later batch forever, with no action from the user at all.
                # routes/jobs.py::patch_job always clears auto_apply_state back to
                # None on any *manual* status change, and _decide_and_apply always
                # sets it to a real value on any *automatic* one — so "status
                # changed but auto_apply_state is still None" is what actually
                # means "the user did this deliberately", and is the only case
                # that should keep a batch from ever reconsidering the job again.
                if job.status != JobStatus.discovered and job.auto_apply_state is None:
                    continue
                if _decide_and_apply(
                    job, workspace, batch.auto_apply_threshold, profile, run.id, db
                ):
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
