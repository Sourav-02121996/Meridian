from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import Workspace
from ..stats import calculate_stats

router = APIRouter(prefix="/api/workspaces/{workspace_id}/stats", tags=["stats"])


@router.get("")
def get_stats(workspace_id: int, db: Session = Depends(get_db)):
    workspace = db.get(Workspace, workspace_id)
    if not workspace:
        raise HTTPException(404, "Workspace not found")
    return calculate_stats(db, workspace_id, workspace.threshold)
