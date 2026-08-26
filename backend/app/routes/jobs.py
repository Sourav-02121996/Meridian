from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..export import build_jobs_workbook
from ..models import Job, JobStatus
from ..schemas import JobOut, JobPatch, SortField, SortOrder

router = APIRouter(prefix="/api/workspaces/{workspace_id}/jobs", tags=["jobs"])


def filtered_jobs_stmt(
    workspace_id: int,
    status: JobStatus | None,
    min_score: float | None,
    q: str,
    auto_apply_state: str | None = None,
):
    stmt = select(Job).where(Job.workspace_id == workspace_id)
    if status:
        stmt = stmt.where(Job.status == status)
    if min_score is not None:
        stmt = stmt.where(Job.score >= min_score)
    if auto_apply_state:
        stmt = stmt.where(Job.auto_apply_state == auto_apply_state)
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
    db: Session = Depends(get_db),
):
    stmt = filtered_jobs_stmt(workspace_id, status, min_score, q, auto_apply_state)
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
    db.commit()
    db.refresh(job)
    return job
