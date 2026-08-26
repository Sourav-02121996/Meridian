from collections import Counter, defaultdict
from datetime import date
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Batch, BatchStatus, Job, JobStatus, Workspace


def calculate_stats(db: Session, workspace_id: int, threshold: float) -> dict:
    jobs = list(db.scalars(select(Job).where(Job.workspace_id == workspace_id)).all())
    scores = [job.score for job in jobs]
    statuses = {status.value: 0 for status in JobStatus}
    statuses.update(Counter(job.status.value for job in jobs))
    buckets = [
        {"bucket": f"{start}-{start + 9 if start < 90 else 100}", "count": 0}
        for start in range(0, 100, 10)
    ]
    for score in scores:
        buckets[min(int(score // 10), 9)]["count"] += 1
    ats: dict[str, list[float]] = defaultdict(list)
    applied: Counter[date] = Counter()
    for job in jobs:
        ats[job.ats_platform].append(job.score)
        if job.date_applied:
            applied[job.date_applied.date()] += 1
    return {
        "total": len(jobs),
        "by_status": statuses,
        "above_threshold": sum(score >= threshold for score in scores),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "median_score": round(median(scores), 1) if scores else 0,
        "score_histogram": buckets,
        "by_ats": [
            {"ats": name, "count": len(values), "avg_score": round(sum(values) / len(values), 1)}
            for name, values in sorted(ats.items())
        ],
        "applied_over_time": _cumulative_applications(applied),
    }


def _cumulative_applications(applied: Counter[date]) -> list[dict]:
    total = 0
    points = []
    for day in sorted(applied):
        total += applied[day]
        points.append({"date": day.isoformat(), "count": total})
    return points


def calculate_dashboard(db: Session) -> dict:
    """Aggregate metrics across every workspace, for the global Dashboard page."""
    workspaces = list(db.scalars(select(Workspace)).all())
    jobs = list(db.scalars(select(Job)).all())
    batches = list(db.scalars(select(Batch)).all())
    jobs_by_workspace: dict[int, list[Job]] = defaultdict(list)
    for job in jobs:
        jobs_by_workspace[job.workspace_id].append(job)
    by_workspace = [
        {
            "workspace_id": workspace.id,
            "name": workspace.name,
            "total_jobs": len(jobs_by_workspace[workspace.id]),
            "applied": sum(
                1 for job in jobs_by_workspace[workspace.id] if job.status == JobStatus.applied
            ),
            "applied_auto": sum(
                1
                for job in jobs_by_workspace[workspace.id]
                if job.auto_apply_state == "applied_auto"
            ),
            "above_threshold": sum(
                1 for job in jobs_by_workspace[workspace.id] if job.score >= workspace.threshold
            ),
        }
        for workspace in workspaces
    ]
    return {
        "workspace_count": len(workspaces),
        "total_jobs": len(jobs),
        "applied_total": sum(1 for job in jobs if job.status == JobStatus.applied),
        "applied_auto_total": sum(1 for job in jobs if job.auto_apply_state == "applied_auto"),
        "needs_review_total": sum(1 for job in jobs if job.auto_apply_state == "needs_review"),
        "active_batches": sum(1 for batch in batches if batch.status == BatchStatus.active),
        "total_batches": len(batches),
        "by_workspace": by_workspace,
    }
