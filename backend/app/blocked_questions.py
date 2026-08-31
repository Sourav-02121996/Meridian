"""Per-job bookkeeping for a still-unresolved required question, surfaced in the
Needs-Review panel (BlockedQuestionsPanel.tsx) for a human to answer once and
retry. Split out of the old qa_bank.py module on purpose: this workflow is
independent of (and outlives) the workspace-wide Q&A bank that used to live
alongside it — see models.JobBlockedQuestion's own docstring for why the bank was
retired while this stays.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from .apply_adapters import QuestionDescriptor
from .models import Job, JobBlockedQuestion


def record_blocked_questions(
    db: Session, job: Job, workspace_id: int, questions: list[QuestionDescriptor]
) -> list[JobBlockedQuestion]:
    """Upserts one pending JobBlockedQuestion per still-unresolved question from this
    attempt, keyed by (job_id, question_text) — a job re-touched by a later batch
    run before the user answers it just refreshes the existing pending row's
    options/updated_at instead of spawning a duplicate."""
    existing = {
        row.question_text: row
        for row in db.scalars(
            select(JobBlockedQuestion).where(JobBlockedQuestion.job_id == job.id)
        ).all()
    }
    created: list[JobBlockedQuestion] = []
    for question in questions:
        row = existing.get(question.label)
        if row is not None:
            row.options = question.options
            row.field_type = question.field_type
            # This exact question is unresolved *again*. Reopen it so the UI
            # cannot hide it while the job still says custom_questions, but retain
            # the applicant's previously approved answer. Clearing it made a
            # failed constrained selection (for example, an ambiguous city) look
            # as though the applicant had never answered the question at all.
            if row.status == "approved":
                row.status = "pending"
            elif row.status == "dismissed":
                row.status = "pending"
            created.append(row)
            continue
        row = JobBlockedQuestion(
            job_id=job.id,
            workspace_id=workspace_id,
            question_text=question.label,
            field_type=question.field_type,
            options=question.options,
            status="pending",
        )
        db.add(row)
        created.append(row)
    return created
