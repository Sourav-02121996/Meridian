from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import BatchIntervalUnit, BatchRepeatMode, BatchSource, BatchStatus, JobStatus


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    external_id: str
    title: str
    company: str
    ats_platform: str
    apply_url: str
    description: str
    score: float
    requirement_coverage: float
    skill_coverage: float
    global_similarity: float
    matched_skills: list[str]
    missing_skills: list[str]
    weak_requirements: list[str]
    status: JobStatus
    auto_apply_state: str | None
    review_reason: str | None
    date_fetched: datetime
    date_scored: datetime | None
    date_applied: datetime | None
    created_at: datetime
    updated_at: datetime


class JobPatch(BaseModel):
    status: JobStatus


class ScrapeRequest(BaseModel):
    query: str = "Software Engineer"
    days: int = Field(2, ge=1, le=30)
    max_jobs: int = Field(100, ge=1, le=1000)


class ResumeRequest(BaseModel):
    text: str


class ThresholdRequest(BaseModel):
    value: float = Field(ge=0, le=100)


class AutoApplyThresholdRequest(BaseModel):
    value: float = Field(ge=0, le=100)


class ProfileRequest(BaseModel):
    name: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""
    cover_letter: str = ""


SortField = Literal["score", "date"]
SortOrder = Literal["asc", "desc"]


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceRename(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime
    job_count: int = 0
    applied_count: int = 0
    above_threshold: int = 0


class WorkspaceSettingsOut(BaseModel):
    resume: str
    resume_filename: str | None
    has_resume_file: bool
    threshold: float
    auto_apply_threshold: float
    profile_name: str
    profile_email: str
    profile_phone: str
    profile_linkedin: str
    cover_letter: str


class BatchCreate(BaseModel):
    workspace_id: int
    query: str = "Software Engineer"
    days: int = Field(2, ge=1, le=30)
    max_jobs: int = Field(100, ge=1, le=1000)
    # None => a single one-off run at start_at.
    interval_unit: BatchIntervalUnit | None = None
    start_at: datetime
    repeat_mode: BatchRepeatMode = BatchRepeatMode.count
    run_limit: int | None = Field(None, ge=1, le=1000)
    auto_apply_threshold: float = Field(95, ge=0, le=100)


class BatchPatch(BaseModel):
    status: BatchStatus


class BatchOut(BaseModel):
    id: int
    workspace_id: int
    workspace_name: str = ""
    query: str
    days: int
    max_jobs: int
    interval_unit: BatchIntervalUnit | None
    start_at: datetime
    repeat_mode: BatchRepeatMode
    run_limit: int | None
    runs_completed: int
    auto_apply_threshold: float
    source: BatchSource
    status: BatchStatus
    next_run_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BatchRunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    batch_id: int
    started_at: datetime
    finished_at: datetime | None
    fetched: int
    new: int
    updated: int
    auto_applied: int
    needs_review: int
    status: str
    error: str | None
