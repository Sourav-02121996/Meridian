"""Unit tests for scheduler._make_answer_lookup — the full tier chain that lets
attempt_apply fill and submit a field with no human review:

  0. this exact job's own previously human-approved answer (_approved_answers_for_job)
  B. profile_similarity.match_profile_field
  C. resume_rag.draft_from_resume_rag (if llm_answer_drafting_enabled)
  D. llm_drafting.draft_educated_guess (if llm_educated_guess_enabled)

Uses a real in-memory DB (same pattern as test_scheduler_run_batch.py) so
_approved_answers_for_job's actual query is exercised, not mocked away — but every
tier function itself is monkeypatched, so this never touches a real embedding
model or Ollama instance.
"""

from unittest.mock import patch

import app.scheduler as scheduler_module
from app.apply_adapters import AnswerAttempt, QuestionDescriptor
from app.config import get_settings
from app.db import Base
from app.models import Job, JobBlockedQuestion, Workspace
from app.scheduler import _make_answer_lookup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

QUESTION = QuestionDescriptor(label="Are you open to relocation?", field_type="text")
PROFILE = {"location": "Boston, MA, USA"}


def _memory_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()


def _job(db, **overrides):
    workspace = Workspace(name="W")
    db.add(workspace)
    db.commit()
    job = Job(
        workspace_id=workspace.id,
        external_id="ext-1",
        title=overrides.get("title", "Software Engineer"),
        company=overrides.get("company", "Twitch"),
        description=overrides.get("description", "Build cool things."),
        apply_url="https://example.com/job",
        ats_platform="greenhouse",
        score=100,
    )
    db.add(job)
    db.commit()
    return job


def _settings(monkeypatch, **flags):
    settings = get_settings()
    for key, value in flags.items():
        monkeypatch.setattr(settings, key, value)
    return settings


def test_human_approved_answer_short_circuits_everything(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=True, llm_educated_guess_enabled=True)
    db = _memory_session()
    job = _job(db)
    db.add(
        JobBlockedQuestion(
            job_id=job.id,
            workspace_id=job.workspace_id,
            question_text=QUESTION.label,
            field_type="text",
            status="approved",
            answer_text="Yes, definitely",
        )
    )
    db.commit()
    workspace = db.get(Workspace, job.workspace_id)

    lookup = _make_answer_lookup(db, job, workspace, PROFILE)
    with (
        patch.object(scheduler_module, "match_profile_field") as mock_profile,
        patch.object(scheduler_module, "draft_from_resume_rag") as mock_rag,
        patch.object(scheduler_module, "draft_educated_guess") as mock_guess,
    ):
        result = lookup(QUESTION)

    assert result == AnswerAttempt(value="Yes, definitely", source="human_approved")
    mock_profile.assert_not_called()
    mock_rag.assert_not_called()
    mock_guess.assert_not_called()


def test_pending_or_dismissed_blocked_question_is_not_treated_as_approved(monkeypatch):
    db = _memory_session()
    job = _job(db)
    db.add(
        JobBlockedQuestion(
            job_id=job.id,
            workspace_id=job.workspace_id,
            question_text=QUESTION.label,
            field_type="text",
            status="pending",
            drafted_answer="A pending draft, not yet approved",
        )
    )
    db.commit()
    workspace = db.get(Workspace, job.workspace_id)

    lookup = _make_answer_lookup(db, job, workspace, PROFILE)
    with patch.object(scheduler_module, "match_profile_field", return_value=None):
        result = lookup(QUESTION)
    assert result is None


def test_falls_back_to_profile_match_when_no_approved_answer(monkeypatch):
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    profile_attempt = AnswerAttempt(value="Yes", source="profile", confidence=0.9)
    with (
        patch.object(
            scheduler_module, "match_profile_field", return_value=profile_attempt
        ) as mock_profile,
        patch.object(scheduler_module, "draft_from_resume_rag") as mock_rag,
    ):
        result = lookup(QUESTION)

    assert result is profile_attempt
    mock_profile.assert_called_once_with(QUESTION, PROFILE)
    mock_rag.assert_not_called()


def test_falls_back_to_resume_rag_when_profile_has_no_match(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=True)
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    rag_attempt = AnswerAttempt(value="Yes", source="resume_rag")
    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(
            scheduler_module, "draft_from_resume_rag", return_value=rag_attempt
        ) as mock_rag,
        patch.object(scheduler_module, "draft_educated_guess") as mock_guess,
    ):
        result = lookup(QUESTION)

    assert result is rag_attempt
    mock_rag.assert_called_once_with(QUESTION, workspace.resume_text, PROFILE)
    mock_guess.assert_not_called()


def test_resume_rag_never_called_when_drafting_disabled(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=False, llm_educated_guess_enabled=True)
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(scheduler_module, "draft_from_resume_rag") as mock_rag,
        patch.object(scheduler_module, "draft_educated_guess", return_value="Yes"),
    ):
        lookup(QUESTION)

    mock_rag.assert_not_called()


def test_falls_back_to_educated_guess_when_everything_else_abstains(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=True, llm_educated_guess_enabled=True)
    db = _memory_session()
    job = _job(db, title="Software Engineer I", company="Twitch", description="Ship features.")
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(scheduler_module, "draft_from_resume_rag", return_value=None),
        patch.object(scheduler_module, "draft_educated_guess", return_value="Yes") as mock_guess,
    ):
        result = lookup(QUESTION)

    assert result == AnswerAttempt(value="Yes", source="llm_guess")
    mock_guess.assert_called_once_with(
        QUESTION,
        workspace.resume_text,
        PROFILE,
        job_title="Software Engineer I",
        job_company="Twitch",
        job_description="Ship features.",
    )


def test_educated_guess_never_called_when_disabled(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=True, llm_educated_guess_enabled=False)
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(scheduler_module, "draft_from_resume_rag", return_value=None),
        patch.object(scheduler_module, "draft_educated_guess") as mock_guess,
    ):
        result = lookup(QUESTION)

    assert result is None
    mock_guess.assert_not_called()


def test_no_answer_when_every_tier_abstains(monkeypatch):
    _settings(monkeypatch, llm_answer_drafting_enabled=True, llm_educated_guess_enabled=True)
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(scheduler_module, "draft_from_resume_rag", return_value=None),
        patch.object(scheduler_module, "draft_educated_guess", return_value=None),
    ):
        result = lookup(QUESTION)

    assert result is None


def test_generated_choice_answer_requires_human_approval(monkeypatch):
    """An LLM may draft a radio/select suggestion for review, but the live retry
    lookup must never submit that factual choice without approval."""
    _settings(monkeypatch, llm_answer_drafting_enabled=True, llm_educated_guess_enabled=True)
    db = _memory_session()
    job = _job(db)
    workspace = db.get(Workspace, job.workspace_id)
    choice = QuestionDescriptor(
        label="Are you based in the listed office location?",
        field_type="radio",
        options=["Yes, I am based there", "No, but I will relocate"],
    )
    lookup = _make_answer_lookup(db, job, workspace, PROFILE)

    with (
        patch.object(scheduler_module, "match_profile_field", return_value=None),
        patch.object(scheduler_module, "draft_from_resume_rag") as mock_rag,
        patch.object(scheduler_module, "draft_educated_guess") as mock_guess,
    ):
        result = lookup(choice)

    assert result is None
    mock_rag.assert_not_called()
    mock_guess.assert_not_called()
