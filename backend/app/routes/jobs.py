from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, delete, desc, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..export import build_jobs_workbook
from ..models import Job, JobBlockedQuestion, JobStatus
from ..scheduler import retry_apply_job
from ..schemas import (
    BlockedQuestionAnswer,
    JobBlockedQuestionOut,
    JobOut,
    JobPatch,
    SortField,
    SortOrder,
)

router = APIRouter(prefix="/api/workspaces/{workspace_id}/jobs", tags=["jobs"])


def filtered_jobs_stmt(
    workspace_id: int,
    status: JobStatus | None,
    min_score: float | None,
    q: str,
    auto_apply_state: str | None = None,
    batch_id: int | None = None,
):
    stmt = select(Job).where(Job.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if min_score is not None:
        stmt = stmt.where(Job.score >= min_score)
    if auto_apply_state:
        stmt = stmt.where(Job.auto_apply_state == auto_apply_state)
    if batch_id is not None:
        # Only ever populated for a job imported by an upload batch (see
        # sheet_import.py::build_jobs_from_rows) — a search-sourced batch never
        # sets this on the jobs it discovers, so this filter only ever narrows
        # down to "the jobs from this specific upload", never anything wider.
        stmt = stmt.where(Job.source_batch_id == batch_id)
    if q.strip():
        term = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Job.title.ilike(term), Job.company.ilike(term), Job.description.ilike(term))
        )
    return stmt


@router.get("", response_model=list[JobOut])
def list_jobs(
    workspace_id: int,
    status: JobStatus | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    sort: SortField = "score",
    order: SortOrder = "desc",
    q: str = "",
    auto_apply_state: str | None = None,
    batch_id: int | None = None,
    db: Session = Depends(get_db),
):
    stmt = filtered_jobs_stmt(workspace_id, status, min_score, q, auto_apply_state, batch_id)
    column = Job.score if sort == "score" else Job.date_fetched
    stmt = stmt.order_by((desc if order == "desc" else asc)(column))
    return list(db.scalars(stmt).all())


@router.get("/export")
def export_jobs(
    workspace_id: int,
    status: JobStatus | None = None,
    min_score: float | None = Query(None, ge=0, le=100),
    q: str = "",
    db: Session = Depends(get_db),
):
    jobs = list(db.scalars(filtered_jobs_stmt(workspace_id, status, min_score, q)).all())
    filename = f"meridian_jobs_{date.today().isoformat()}.xlsx"
    return StreamingResponse(
        build_jobs_workbook(jobs),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{job_id}", response_model=JobOut)
def get_job(workspace_id: int, job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.workspace_id != workspace_id:
        raise HTTPException(404, "Job not found")
    return job


@router.patch("/{job_id}", response_model=JobOut)
def patch_job(workspace_id: int, job_id: int, payload: JobPatch, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.workspace_id != workspace_id:
        raise HTTPException(404, "Job not found")
    was_applied = job.status == JobStatus.applied
    job.status = payload.status
    if payload.status == JobStatus.applied and not was_applied:
        job.date_applied = datetime.now(timezone.utc)
    elif payload.status != JobStatus.applied:
        job.date_applied = None
    # A manual decision (Apply/Skip) resolves any pending auto-apply review on this job.
    job.auto_apply_state = None
    job.review_reason = None
    job.last_apply_detail = None
    db.commit()
    db.refresh(job)
    return job


@router.post("/{job_id}/retry-apply", status_code=202)
def retry_apply(
    workspace_id: int, job_id: int, background: BackgroundTasks, db: Session = Depends(get_db)
):
    """Actually re-attempts the automated submission for one job, right now —
    distinct from PATCH's status update, which only ever does manual bookkeeping
    (see JobPatch's own handling below) and never launches a browser. The
    intended use is right after approving a blocked question's answer, but any
    needs_review job can be retried; attempt_apply re-evaluates everything fresh
    each time, so a still-unresolved case just reports the same (or a new)
    review_reason rather than erroring."""
    job = _get_job_or_404(workspace_id, job_id, db)
    if job.auto_apply_state != "needs_review":
        raise HTTPException(400, "Only a job currently needing review can be retried.")
    # Persist the transition before returning 202. The frontend can now poll a
    # durable state instead of refetching the old needs_review row while the
    # background browser is still starting up.
    job.auto_apply_state = "applying"
    job.review_reason = None
    job.last_apply_started_at = datetime.now(timezone.utc)
    job.last_apply_finished_at = None
    job.last_apply_detail = None
    db.commit()
    background.add_task(retry_apply_job, job.id)
    return {"started": True, "state": "applying"}


@router.delete("/{job_id}", status_code=204)
def delete_job(workspace_id: int, job_id: int, db: Session = Depends(get_db)):
    """Hard delete — the job row and its blocked-question rows are actually
    removed, not just hidden, so re-adding the same posting later (a fresh
    upload or a re-discovered search result) starts from nothing instead of
    inheriting stale auto_apply_state/review_reason/blocked-question history.
    JobBlockedQuestion has an FK to jobs.id with no cascade configured (see
    models.py), so its rows for this job must be deleted explicitly first."""
    job = db.get(Job, job_id)
    if not job or job.workspace_id != workspace_id:
        raise HTTPException(404, "Job not found")
    db.execute(delete(JobBlockedQuestion).where(JobBlockedQuestion.job_id == job_id))
    db.delete(job)
    db.commit()


def _get_job_or_404(workspace_id: int, job_id: int, db: Session) -> Job:
    job = db.get(Job, job_id)
    if not job or job.workspace_id != workspace_id:
        raise HTTPException(404, "Job not found")
    return job


def _get_blocked_question_or_404(job_id: int, bq_id: int, db: Session) -> JobBlockedQuestion:
    bq = db.get(JobBlockedQuestion, bq_id)
    if not bq or bq.job_id != job_id:
        raise HTTPException(404, "Blocked question not found")
    return bq


@router.get("/{job_id}/blocked-questions", response_model=list[JobBlockedQuestionOut])
def list_blocked_questions(workspace_id: int, job_id: int, db: Session = Depends(get_db)):
    _get_job_or_404(workspace_id, job_id, db)
    stmt = (
        select(JobBlockedQuestion)
        .where(JobBlockedQuestion.job_id == job_id)
        .order_by(JobBlockedQuestion.created_at, JobBlockedQuestion.id)
    )
    return list(db.scalars(stmt).all())


@router.post(
    "/{job_id}/blocked-questions/{bq_id}/answer",
    response_model=JobBlockedQuestionOut,
)
def answer_blocked_question(
    workspace_id: int,
    job_id: int,
    bq_id: int,
    payload: BlockedQuestionAnswer,
    db: Session = Depends(get_db),
):
    _get_job_or_404(workspace_id, job_id, db)
    bq = _get_blocked_question_or_404(job_id, bq_id, db)
    answer = payload.answer_text.strip()
    if bq.field_type in ("select", "radio") and bq.options:
        live_options = {opt.strip().lower() for opt in bq.options}
        if answer.lower() not in live_options:
            raise HTTPException(422, "Answer must be one of the question's own options")
    bq.answer_text = answer
    bq.status = "approved"
    # Once a human saves an answer, an older model suggestion must not continue
    # appearing beside it (and potentially be re-selected on a later edit).
    bq.drafted_answer = None
    bq.drafted_by_model = None
    # Deliberately per-job only — approving an answer here no longer persists it
    # into any workspace-wide store for reuse on other jobs (the Q&A bank this used
    # to write into was retired; see models.JobBlockedQuestion's docstring for why).
    # A "Retry auto-apply" on *this* job picks it up because find_unhandled_
    # required_fields/answer_lookup re-reads this same row's answer_text fresh.
    db.commit()
    db.refresh(bq)
    return bq


@router.post(
    "/{job_id}/blocked-questions/{bq_id}/dismiss",
    response_model=JobBlockedQuestionOut,
)
def dismiss_blocked_question(
    workspace_id: int, job_id: int, bq_id: int, db: Session = Depends(get_db)
):
    _get_job_or_404(workspace_id, job_id, db)
    bq = _get_blocked_question_or_404(job_id, bq_id, db)
    bq.status = "dismissed"
    db.commit()
    db.refresh(bq)
    return bq
