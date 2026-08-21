from collections import Counter, defaultdict
from datetime import date
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Job, JobStatus


def calculate_stats(db: Session, threshold: float) -> dict:
    jobs = list(db.scalars(select(Job)).all())
    scores = [job.score for job in jobs]
    statuses = {status.value: 0 for status in JobStatus}
    statuses.update(Counter(job.status.value for job in jobs))
    buckets = [{"bucket": f"{start}-{start + 9 if start < 90 else 100}", "count": 0} for start in range(0, 100, 10)]
    for score in scores:
        buckets[min(int(score // 10), 9)]["count"] += 1
    ats: dict[str, list[float]] = defaultdict(list)
    applied: Counter[date] = Counter()
    for job in jobs:
        ats[job.ats_platform].append(job.score)
        if job.date_applied:
            applied[job.date_applied.date()] += 1
    return {
        "total": len(jobs), "by_status": statuses,
        "above_threshold": sum(score >= threshold for score in scores),
        "avg_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "median_score": round(median(scores), 1) if scores else 0,
        "score_histogram": buckets,
        "by_ats": [{"ats": name, "count": len(values), "avg_score": round(sum(values) / len(values), 1)} for name, values in sorted(ats.items())],
        "applied_over_time": _cumulative_applications(applied),
    }


def _cumulative_applications(applied: Counter[date]) -> list[dict]:
    total = 0
    points = []
    for day in sorted(applied):
        total += applied[day]
        points.append({"date": day.isoformat(), "count": total})
    return points
