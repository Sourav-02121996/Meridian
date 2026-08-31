from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import scheduler as scheduler_module
from ..db import get_db
from ..models import Batch, BatchRepeatMode, BatchRun, BatchSource, BatchStatus, Job, Workspace
from ..scheduler import run_batch, schedule_batch, unschedule_batch
from ..schemas import BatchCreate, BatchOut, BatchPatch, BatchRunOut
from ..sheet_import import SheetImportError, parse_sheet, upsert_jobs_from_rows

router = APIRouter(prefix="/api/batches", tags=["batches"])


def _out(batch: Batch, workspace_name: str) -> BatchOut:
    return BatchOut(
        id=batch.id,
        workspace_id=batch.workspace_id,
        workspace_name=workspace_name,
        query=batch.query,
        days=batch.days,
        max_jobs=batch.max_jobs,
        job_title_query=batch.job_title_query,
        technology_keywords_query=batch.technology_keywords_query,
        job_description_query=batch.job_description_query,
        departments=batch.departments,
        seniority=batch.seniority,
        interval_unit=batch.interval_unit,
        start_at=batch.start_at,
        repeat_mode=batch.repeat_mode,
        run_limit=batch.run_limit,
        runs_completed=batch.runs_completed,
        auto_apply_threshold=batch.auto_apply_threshold,
        source=batch.source,
        status=batch.status,
        next_run_at=scheduler_module.next_run_at(batch.id)
        if batch.status == BatchStatus.active
        else None,
        created_at=batch.created_at,
        updated_at=batch.updated_at,
    )


@router.post("", response_model=BatchOut, status_code=201)
def create_batch(payload: BatchCreate, db: Session = Depends(get_db)):
    workspace = db.get(Workspace, payload.workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    if payload.interval_unit is None:
        # A one-off run always fires exactly once, regardless of what was submitted.
        repeat_mode, run_limit = BatchRepeatMode.count, 1
    else:
        repeat_mode, run_limit = payload.repeat_mode, payload.run_limit
        if repeat_mode == BatchRepeatMode.count and not run_limit:
            raise HTTPException(
                400, "Set how many times a recurring batch should run, or choose indefinite."
            )
    batch = Batch(
        workspace_id=workspace.id,
        query=payload.query,
        days=payload.days,
        max_jobs=payload.max_jobs,
        job_title_query=payload.job_title_query,
        technology_keywords_query=payload.technology_keywords_query,
        job_description_query=payload.job_description_query,
        departments=payload.departments,
        seniority=payload.seniority,
        interval_unit=payload.interval_unit,
        start_at=payload.start_at,
        repeat_mode=repeat_mode,
        run_limit=run_limit,
        auto_apply_threshold=payload.auto_apply_threshold,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    schedule_batch(batch)
    return _out(batch, workspace.name)


@router.post("/upload", response_model=BatchOut, status_code=201)
async def create_upload_batch(
    workspace_id: int = Form(...),
    auto_apply_threshold: float = Form(95),
    start_at: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    try:
        start = datetime.fromisoformat(start_at)
    except ValueError as exc:
        raise HTTPException(400, "Invalid start date/time.") from exc
    contents = await file.read()
    try:
        rows = parse_sheet(file.filename or "", contents)
    except SheetImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not rows:
        raise HTTPException(400, "No rows with an apply link were found in the file.")

    batch = Batch(
        workspace_id=workspace.id,
        query=f"Uploaded list: {file.filename}",
        days=0,
        max_jobs=len(rows),
        interval_unit=None,
        start_at=start,
        repeat_mode=BatchRepeatMode.count,
        run_limit=1,
        auto_apply_threshold=auto_apply_threshold,
        source=BatchSource.upload,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    upsert_jobs_from_rows(db, rows, workspace.id, batch.id)
    db.commit()
    schedule_batch(batch)
    return _out(batch, workspace.name)


@router.get("", response_model=list[BatchOut])
def list_batches(db: Session = Depends(get_db)):
    batches = list(db.scalars(select(Batch).order_by(Batch.created_at.desc())).all())
    names = {workspace.id: workspace.name for workspace in db.scalars(select(Workspace)).all()}
    return [_out(batch, names.get(batch.workspace_id, "")) for batch in batches]


@router.get("/{batch_id}", response_model=BatchOut)
def get_batch(batch_id: int, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    workspace = db.get(Workspace, batch.workspace_id)
    return _out(batch, workspace.name if workspace else "")


@router.patch("/{batch_id}", response_model=BatchOut)
def patch_batch(batch_id: int, payload: BatchPatch, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if payload.status == BatchStatus.active:
        batch.status = BatchStatus.active
        db.commit()
        db.refresh(batch)
        schedule_batch(batch)
    elif payload.status == BatchStatus.paused:
        batch.status = BatchStatus.paused
        db.commit()
        unschedule_batch(batch.id)
    else:
        raise HTTPException(400, "Unsupported status transition.")
    workspace = db.get(Workspace, batch.workspace_id)
    return _out(batch, workspace.name if workspace else "")


@router.delete("/{batch_id}", status_code=204)
def delete_batch(batch_id: int, db: Session = Depends(get_db)):
    """Jobs an upload batch imported are left in place — they're independent
    results, not part of the batch itself — but their `source_batch_id` back-
    reference must be cleared here, not just left dangling. `batches.id` has no
    AUTOINCREMENT, so once this row is gone the id can be reused by a completely
    unrelated future batch; without this, that new batch's own run would silently
    inherit these old jobs as if they were its own (see scheduler._run_batch's
    workspace-scoped query, which guards the same failure mode from the other
    side, for a job that slips through some other way)."""
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    unschedule_batch(batch.id)
    db.query(BatchRun).filter(BatchRun.batch_id == batch.id).delete()
    db.query(Job).filter(Job.source_batch_id == batch.id).update({"source_batch_id": None})
    db.delete(batch)
    db.commit()


@router.get("/{batch_id}/runs", response_model=list[BatchRunOut])
def list_runs(batch_id: int, db: Session = Depends(get_db)):
    return list(
        db.scalars(
            select(BatchRun)
            .where(BatchRun.batch_id == batch_id)
            .order_by(BatchRun.started_at.desc())
        ).all()
    )


@router.post("/{batch_id}/run-now", status_code=202)
def run_now(batch_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    batch = db.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    background.add_task(run_batch, batch_id)
    return {"started": True}
