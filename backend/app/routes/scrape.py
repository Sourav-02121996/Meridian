import logging
from datetime import datetime, timezone
from threading import Lock

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crawler import crawl
from ..db import SessionLocal, get_db
from ..extractor import extract_job
from ..models import Job, Setting
from ..schemas import ScrapeRequest
from ..scorer import score_job

router = APIRouter(prefix="/api/scrape", tags=["scrape"])
log = logging.getLogger("meridian.scrape")
_lock = Lock()
_status = {"running": False, "collected": 0, "done": False, "error": None, "result": None}


def _set_status(**values):
    with _lock:
        _status.update(values)


def _run_discovery(payload: ScrapeRequest):
    try:
        _set_status(running=True, collected=0, done=False, error=None, result=None)
        with SessionLocal() as db:
            resume = db.get(Setting, "resume").value
            threshold_row = db.get(Setting, "threshold")
            threshold = (
                float(threshold_row.value) if threshold_row else get_settings().score_threshold
            )
            raw_jobs = crawl(
                payload.query,
                payload.days,
                target=payload.max_jobs,
                progress=lambda count: _set_status(collected=count),
            )
            extracted_jobs = {}
            for raw in raw_jobs:
                extracted = extract_job(raw)
                extracted_jobs[extracted["external_id"]] = extracted
            existing = (
                {
                    job.external_id: job
                    for job in db.scalars(
                        select(Job).where(Job.external_id.in_(extracted_jobs))
                    ).all()
                }
                if extracted_jobs
                else {}
            )
            new = updated = above = 0
            for index, extracted in enumerate(extracted_jobs.values(), 1):
                scoring = score_job(extracted["description"], resume)
                job = existing.get(extracted["external_id"])
                if job:
                    updated += 1
                else:
                    job = Job(external_id=extracted["external_id"])
                    db.add(job)
                    existing[extracted["external_id"]] = job
                    new += 1
                for key, value in {**extracted, **scoring}.items():
                    setattr(job, key, value)
                now = datetime.now(timezone.utc)
                job.date_fetched = now
                job.date_scored = now
                above += scoring["score"] >= threshold
                if index % 10 == 0:
                    log.info("Scored %s/%s jobs", index, len(extracted_jobs))
            db.commit()
            result = {
                "fetched": len(raw_jobs),
                "new": new,
                "updated": updated,
                "above_threshold": above,
            }
        _set_status(running=False, done=True, result=result)
    except Exception as exc:
        log.exception("Discovery failed")
        _set_status(running=False, done=True, error=str(exc))


@router.post("", status_code=202)
def discover(payload: ScrapeRequest, background: BackgroundTasks, db: Session = Depends(get_db)):
    resume = db.get(Setting, "resume")
    if not resume or not resume.value.strip():
        raise HTTPException(
            400, "Save your resume before discovering jobs so Meridian can score matches."
        )
    with _lock:
        if _status["running"]:
            raise HTTPException(409, "A discovery run is already in progress.")
        _status.update(running=True, collected=0, done=False, error=None, result=None)
    background.add_task(_run_discovery, payload)
    return {"started": True}


@router.get("/status")
def discovery_status():
    with _lock:
        return dict(_status)
