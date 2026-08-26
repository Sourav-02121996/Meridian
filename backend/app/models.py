import enum
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    discovered = "discovered"
    to_apply = "to_apply"
    applied = "applied"
    skipped = "skipped"


class BatchIntervalUnit(str, enum.Enum):
    hour = "hour"
    day = "day"
    week = "week"


class BatchRepeatMode(str, enum.Enum):
    count = "count"
    indefinite = "indefinite"


class BatchStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    completed = "completed"


class BatchSource(str, enum.Enum):
    search = "search"  # crawl HiringCafe and score against the resume
    upload = "upload"  # jobs come from an uploaded xlsx/csv of apply links


class Workspace(Base):
    """One named project: its own resume, thresholds, applicant profile, and job pipeline."""

    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Unused until the real auth phase lands; kept nullable so workspaces can be tied to an
    # account later without a schema rewrite.
    owner_email: Mapped[str | None] = mapped_column(String(320), nullable=True, index=True)
    resume_text: Mapped[str] = mapped_column(Text, default="")
    resume_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resume_file: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    threshold: Mapped[float] = mapped_column(Float, default=82.0)
    auto_apply_threshold: Mapped[float] = mapped_column(Float, default=95.0)
    profile_name: Mapped[str] = mapped_column(String(200), default="")
    profile_email: Mapped[str] = mapped_column(String(320), default="")
    profile_phone: Mapped[str] = mapped_column(String(50), default="")
    profile_linkedin: Mapped[str] = mapped_column(String(300), default="")
    profile_portfolio_url: Mapped[str] = mapped_column(String(300), default="")
    profile_github_url: Mapped[str] = mapped_column(String(300), default="")
    profile_location: Mapped[str] = mapped_column(String(200), default="")
    profile_current_company: Mapped[str] = mapped_column(String(200), default="")
    profile_current_title: Mapped[str] = mapped_column(String(200), default="")
    profile_desired_salary: Mapped[str] = mapped_column(String(100), default="")
    profile_start_date: Mapped[str] = mapped_column(String(100), default="")
    # Eligibility answers, stored as "Yes" / "No" / "" (unanswered) rather than a strict
    # boolean so an unfilled field is distinguishable from an explicit "No".
    profile_work_authorized: Mapped[str] = mapped_column(String(10), default="")
    profile_visa_sponsorship: Mapped[str] = mapped_column(String(10), default="")
    profile_willing_to_relocate: Mapped[str] = mapped_column(String(10), default="")
    profile_18_or_older: Mapped[str] = mapped_column(String(10), default="")
    # Voluntary self-identification — legally protected characteristics. Left blank by
    # default and never inferred or guessed; only sent to a form if explicitly filled in.
    profile_gender: Mapped[str] = mapped_column(String(100), default="")
    profile_race_ethnicity: Mapped[str] = mapped_column(String(100), default="")
    profile_veteran_status: Mapped[str] = mapped_column(String(100), default="")
    profile_disability_status: Mapped[str] = mapped_column(String(100), default="")
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("workspace_id", "external_id", name="uq_job_workspace_external"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500), default="Untitled role")
    company: Mapped[str] = mapped_column(String(300), default="Unknown company")
    ats_platform: Mapped[str] = mapped_column(String(80), default="career-page")
    apply_url: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(Text, default="")
    score: Mapped[float] = mapped_column(Float, default=0)
    requirement_coverage: Mapped[float] = mapped_column(Float, default=0)
    skill_coverage: Mapped[float] = mapped_column(Float, default=0)
    global_similarity: Mapped[float] = mapped_column(Float, default=0)
    matched_skills: Mapped[list] = mapped_column(JSON, default=list)
    missing_skills: Mapped[list] = mapped_column(JSON, default=list)
    weak_requirements: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.discovered, index=True
    )
    # Set only by the batch scheduler's auto-apply pass; untouched by manual discovery/patches.
    auto_apply_state: Mapped[str | None] = mapped_column(String(30), nullable=True, index=True)
    # One of: below_threshold, unsupported_ats, no_resume_file, custom_questions, form_error.
    review_reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_batch_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("batch_runs.id"), nullable=True
    )
    # Set at import time for rows created by an upload-mode batch, so that batch's
    # scheduled run knows which jobs are its own to evaluate. Null for everything else.
    source_batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("batches.id"), nullable=True, index=True
    )
    date_fetched: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    date_scored: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_applied: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Setting(Base):
    """Legacy global key/value store, superseded by per-workspace columns. Kept only so a
    pre-workspace database still has something for the migration to read from once."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class Batch(Base):
    """A recurring or one-off unattended discovery+auto-apply schedule for one workspace."""

    __tablename__ = "batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id"), index=True)
    query: Mapped[str] = mapped_column(String(300), default="Software Engineer")
    days: Mapped[int] = mapped_column(Integer, default=2)
    max_jobs: Mapped[int] = mapped_column(Integer, default=100)
    # Boolean-query HiringCafe filters — title-only, tech/tools mentioned in the posting,
    # and full description respectively. Empty string means "not set" for all three.
    job_title_query: Mapped[str] = mapped_column(Text, default="")
    technology_keywords_query: Mapped[str] = mapped_column(Text, default="")
    job_description_query: Mapped[str] = mapped_column(Text, default="")
    departments: Mapped[list] = mapped_column(JSON, default=list)
    seniority: Mapped[list] = mapped_column(JSON, default=list)
    # None => a single one-off run at start_at; otherwise the recurring cadence.
    interval_unit: Mapped[BatchIntervalUnit | None] = mapped_column(
        Enum(BatchIntervalUnit), nullable=True
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    repeat_mode: Mapped[BatchRepeatMode] = mapped_column(
        Enum(BatchRepeatMode), default=BatchRepeatMode.count
    )
    run_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    runs_completed: Mapped[int] = mapped_column(Integer, default=0)
    auto_apply_threshold: Mapped[float] = mapped_column(Float, default=95.0)
    source: Mapped[BatchSource] = mapped_column(Enum(BatchSource), default=BatchSource.search)
    status: Mapped[BatchStatus] = mapped_column(
        Enum(BatchStatus), default=BatchStatus.active, index=True
    )
    # Not persisted — computed live from APScheduler's own schedule (see scheduler.next_run_at)
    # so it can never drift out of sync with the actual trigger.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class BatchRun(Base):
    """One execution log entry for a batch: what was fetched and what happened to it."""

    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    updated: Mapped[int] = mapped_column(Integer, default=0)
    auto_applied: Mapped[int] = mapped_column(Integer, default=0)
    needs_review: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="success")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
