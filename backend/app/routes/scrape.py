import logging
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import SessionLocal, get_db
from ..discovery import discover_and_score
from ..models import Workspace
from ..schemas import ScrapeRequest

router = APIRouter(prefix="/api/workspaces/{workspace_id}/scrape", tags=["scrape"])
log = logging.getLogger("meridian.scrape")
_lock = Lock()
_statuses: dict[int, dict] = {}


def _status_for(workspace_id: int) -> dict:
    return _statuses.setdefault(
        workspace_id,
        {"running": False, "collected": 0, "done": False, "error": None, "result": None},
    )


def _set_status(workspace_id: int, **values):
    with _lock:
        _status_for(workspace_id).update(values)


def _run_discovery(workspace_id: int, payload: ScrapeRequest):
    try:
        _set_status(workspace_id, running=True, collected=0, done=False, error=None, result=None)
        with SessionLocal() as db:
            workspace = db.get(Workspace, workspace_id)
            _, result = discover_and_score(
                db,
                workspace,
                payload.query,
                payload.days,
                payload.max_jobs,
                progress=lambda count: _set_status(workspace_id, collected=count),
            )
        _set_status(workspace_id, running=False, done=True, result=result)
    except Exception as exc:
        log.exception("Discovery failed for workspace %s", workspace_id)
        _set_status(workspace_id, running=False, done=True, error=str(exc))


@router.post("", status_code=202)
def discover(
    workspace_id: int,
    payload: ScrapeRequest,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    if not workspace.resume_text.strip():
        raise HTTPException(
            400, "Save your resume before discovering jobs so Meridian can score matches."
        )
    with _lock:
        status = _status_for(workspace_id)
        if status["running"]:
            raise HTTPException(409, "A discovery run is already in progress for this workspace.")
        status.update(running=True, collected=0, done=False, error=None, result=None)
    background.add_task(_run_discovery, workspace_id, payload)
    return {"started": True}


@router.get("/status")
def discovery_status(workspace_id: int):
    with _lock:
        return dict(_status_for(workspace_id))
