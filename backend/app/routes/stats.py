from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..models import Setting
from ..stats import calculate_stats

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
def get_stats(db: Session = Depends(get_db)):
    saved = db.get(Setting, "threshold")
    threshold = float(saved.value) if saved else get_settings().score_threshold
    return calculate_stats(db, threshold)
