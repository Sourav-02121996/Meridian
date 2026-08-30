"""Regression coverage for the phase-2 retry lifecycle and review bookkeeping."""

from datetime import datetime
from unittest.mock import patch

from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.scheduler as scheduler_module
from app.apply_adapters import AutoApplyResult, QuestionDescriptor
from app.blocked_questions import record_blocked_questions
from app.db import Base
from app.models import Job, JobBlockedQuestion, Workspace
from app.routes.jobs import answer_blocked_question, retry_apply
from app.schemas import BlockedQuestionAnswer


def _memory_sessionmaker():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def _workspace_and_job(session_factory, state: str = "needs_review"):
    with session_factory() as db:
        workspace = Workspace(
            name="Software Engineer",
            resume_file=b"%PDF fixture",
            resume_filename="resume.pdf",
            profile_name="Jane Doe",
            profile_email="jane@example.com",
        )
        db.add(workspace)
        db.flush()
        job = Job(
            workspace_id=workspace.id,
            external_id="ashby-job",
            title="Frontend Platform Engineer",
            company="Harvey",
            ats_platform="ashby",
            apply_url="https://jobs.example/application",
            score=100,
            auto_apply_state=state,
            review_reason="custom_questions" if state == "needs_review" else None,
        )
        db.add(job)
        db.commit()
        return workspace.id, job.id


def test_retry_route_persists_applying_before_background_work(monkeypatch):
    session_factory = _memory_sessionmaker()
    workspace_id, job_id = _workspace_and_job(session_factory)
    background = BackgroundTasks()

    with session_factory() as db:
        result = retry_apply(workspace_id, job_id, background, db)
        db.refresh(db.get(Job, job_id))
        job = db.get(Job, job_id)

        assert result == {"started": True, "state": "applying"}
        assert job.auto_apply_state == "applying"
        assert job.review_reason is None
        assert isinstance(job.last_apply_started_at, datetime)
        assert job.last_apply_finished_at is None
        assert len(background.tasks) == 1


def test_retry_worker_persists_server_rejection_detail(monkeypatch):
    session_factory = _memory_sessionmaker()
    _, job_id = _workspace_and_job(session_factory, state="applying")
    monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)

    result = AutoApplyResult(
        False,
        "submission_rejected",
        detail="ATS submit response 422: Location is not specific enough",
    )
    with patch.object(scheduler_module, "attempt_apply", return_value=result):
        scheduler_module._retry_apply_job(job_id)

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.auto_apply_state == "needs_review"
        assert job.review_reason == "submission_rejected"
        assert job.last_apply_detail == result.detail
        assert isinstance(job.last_apply_finished_at, datetime)
        assert job.date_applied is None


def test_retry_worker_persists_explicit_success(monkeypatch):
    session_factory = _memory_sessionmaker()
    _, job_id = _workspace_and_job(session_factory, state="applying")
    monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)

    result = AutoApplyResult(True, detail="ATS submit response 201 from https://ats.test/submit")
    with patch.object(scheduler_module, "attempt_apply", return_value=result):
        scheduler_module._retry_apply_job(job_id)

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.auto_apply_state == "applied_auto"
        assert job.review_reason is None
        assert job.status.value == "applied"
        assert isinstance(job.date_applied, datetime)
        assert isinstance(job.last_apply_finished_at, datetime)
        assert job.last_apply_detail == result.detail


def test_retry_worker_crash_never_leaves_job_applying(monkeypatch):
    session_factory = _memory_sessionmaker()
    _, job_id = _workspace_and_job(session_factory, state="applying")
    monkeypatch.setattr(scheduler_module, "SessionLocal", session_factory)

    with patch.object(scheduler_module, "attempt_apply", side_effect=RuntimeError("browser died")):
        scheduler_module.retry_apply_job(job_id)

    with session_factory() as db:
        job = db.get(Job, job_id)
        assert job.auto_apply_state == "needs_review"
        assert job.review_reason == "unexpected_error"
        assert "RuntimeError: browser died" in job.last_apply_detail
        assert isinstance(job.last_apply_finished_at, datetime)


def test_unresolved_approved_question_is_reopened_without_losing_its_answer():
    session_factory = _memory_sessionmaker()
    workspace_id, job_id = _workspace_and_job(session_factory)
    label = "Which office arrangement applies?"

    with session_factory() as db:
        row = JobBlockedQuestion(
            job_id=job_id,
            workspace_id=workspace_id,
            question_text=label,
            field_type="radio",
            options=["Local", "Relocate"],
            status="approved",
            answer_text="Local",
        )
        db.add(row)
        db.commit()
        job = db.get(Job, job_id)

        [reopened] = record_blocked_questions(
            db,
            job,
            workspace_id,
            [QuestionDescriptor(label=label, field_type="radio", options=["Local", "Relocate"])],
        )

        assert reopened.status == "pending"
        assert reopened.answer_text == "Local"
        assert reopened.drafted_answer is None


def test_editing_an_approved_answer_replaces_stale_ai_draft():
    session_factory = _memory_sessionmaker()
    workspace_id, job_id = _workspace_and_job(session_factory)

    with session_factory() as db:
        row = JobBlockedQuestion(
            job_id=job_id,
            workspace_id=workspace_id,
            question_text="Are you based in the listed location?",
            field_type="radio",
            options=["Yes, based there", "No, willing to relocate"],
            drafted_answer="Yes, based there",
            drafted_by_model="test-model",
            status="approved",
            answer_text="Yes, based there",
        )
        db.add(row)
        db.commit()

        saved = answer_blocked_question(
            workspace_id,
            job_id,
            row.id,
            BlockedQuestionAnswer(answer_text="No, willing to relocate"),
            db,
        )

        assert saved.answer_text == "No, willing to relocate"
        assert saved.status == "approved"
        assert saved.drafted_answer is None
        assert saved.drafted_by_model is None
