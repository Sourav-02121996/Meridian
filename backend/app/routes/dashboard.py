from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..stats import calculate_dashboard

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(db: Session = Depends(get_db)):
    return calculate_dashboard(db)
