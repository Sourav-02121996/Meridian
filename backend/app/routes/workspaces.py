from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Batch, BatchRun, Job, JobStatus, Workspace
from ..scheduler import unschedule_batch
from ..schemas import WorkspaceCreate, WorkspaceOut, WorkspaceRename

router = APIRouter(prefix="/api/workspaces", tags=["workspaces"])


def _summarize(db: Session, workspace: Workspace) -> WorkspaceOut:
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == workspace.id)).all())
    return WorkspaceOut(
        id=workspace.id,
        name=workspace.name,
        created_at=workspace.created_at,
        updated_at=workspace.updated_at,
        job_count=len(jobs),
        applied_count=sum(1 for job in jobs if job.status == JobStatus.applied),
        above_threshold=sum(1 for job in jobs if job.score >= workspace.threshold),
    )


@router.post("", response_model=WorkspaceOut, status_code=201)
def create_workspace(payload: WorkspaceCreate, db: Session = Depends(get_db)):
    settings = get_settings()
    workspace = Workspace(
        name=payload.name.strip(),
        threshold=settings.score_threshold,
        auto_apply_threshold=settings.auto_apply_threshold,
    )
    db.add(workspace)
    db.commit()
    db.refresh(workspace)
    return _summarize(db, workspace)


@router.get("", response_model=list[WorkspaceOut])
def list_workspaces(db: Session = Depends(get_db)):
    workspaces = list(db.scalars(select(Workspace).order_by(Workspace.created_at.asc())).all())
    return [_summarize(db, workspace) for workspace in workspaces]


@router.get("/{workspace_id}", response_model=WorkspaceOut)
def get_workspace(workspace_id: int, db: Session = Depends(get_db)):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return _summarize(db, workspace)


@router.patch("/{workspace_id}", response_model=WorkspaceOut)
def rename_workspace(workspace_id: int, payload: WorkspaceRename, db: Session = Depends(get_db)):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    workspace.name = payload.name.strip()
    workspace.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(workspace)
    return _summarize(db, workspace)


@router.delete("/{workspace_id}", status_code=204)
def delete_workspace(workspace_id: int, db: Session = Depends(get_db)):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    batch_ids = [
        batch.id
        for batch in db.scalars(select(Batch).where(Batch.workspace_id == workspace_id)).all()
    ]
    for batch_id in batch_ids:
        unschedule_batch(batch_id)
        db.query(BatchRun).filter(BatchRun.batch_id == batch_id).delete()
    db.query(Batch).filter(Batch.workspace_id == workspace_id).delete()
    db.query(Job).filter(Job.workspace_id == workspace_id).delete()
    db.delete(workspace)
    db.commit()
