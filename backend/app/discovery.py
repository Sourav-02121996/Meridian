from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .crawler import crawl
from .extractor import extract_job
from .models import Job, Workspace
from .scorer import score_job


def discover_and_score(
    db: Session,
    workspace: Workspace,
    query: str,
    days: int,
    max_jobs: int,
    progress: Callable[[int], None] | None = None,
) -> tuple[list[Job], dict]:
    """Crawl HiringCafe for one workspace, score every result against its resume, and
    upsert jobs scoped to that workspace. Shared by the interactive Discover button and
    the unattended batch scheduler so both stay in sync.
    """
    raw_jobs = crawl(query, days, target=max_jobs, progress=progress)
    extracted_jobs: dict[str, dict] = {}
    for raw in raw_jobs:
        extracted = extract_job(raw)
        extracted_jobs[extracted["external_id"]] = extracted
    existing = (
        {
            job.external_id: job
            for job in db.scalars(
                select(Job).where(
                    Job.workspace_id == workspace.id,
                    Job.external_id.in_(extracted_jobs),
                )
            ).all()
        }
        if extracted_jobs
        else {}
    )
    touched: list[Job] = []
    new = updated = above = 0
    for extracted in extracted_jobs.values():
        scoring = score_job(extracted["description"], workspace.resume_text)
        job = existing.get(extracted["external_id"])
        if job:
            updated += 1
        else:
            job = Job(external_id=extracted["external_id"], workspace_id=workspace.id)
            db.add(job)
            existing[extracted["external_id"]] = job
            new += 1
        for key, value in {**extracted, **scoring}.items():
            setattr(job, key, value)
        now = datetime.now(timezone.utc)
        job.date_fetched = now
        job.date_scored = now
        above += scoring["score"] >= workspace.threshold
        touched.append(job)
    db.commit()
    for job in touched:
        db.refresh(job)
    return touched, {
        "fetched": len(raw_jobs),
        "new": new,
        "updated": updated,
        "above_threshold": above,
    }
