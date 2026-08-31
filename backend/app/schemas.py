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
    last_apply_started_at: datetime | None
    last_apply_finished_at: datetime | None
    last_apply_detail: str | None
    source_batch_id: int | None
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
    job_title_query: str = ""
    technology_keywords_query: str = ""
    job_description_query: str = ""
    departments: list[str] | None = None
    seniority: list[str] | None = None


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
    portfolio_url: str = ""
    github_url: str = ""
    city: str = ""
    state: str = ""
    country: str = ""
    current_company: str = ""
    current_title: str = ""
    desired_salary: str = ""
    start_date: str = ""
    work_authorized: str = ""
    visa_sponsorship: str = ""
    willing_to_relocate: str = ""
    is_18_or_older: str = ""
    gender: str = ""
    race_ethnicity: str = ""
    veteran_status: str = ""
    disability_status: str = ""
    citizenship: str = ""
    security_clearance: str = ""
    background_check_consent: str = ""
    drug_test_consent: str = ""
    criminal_history: str = ""
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
    profile_portfolio_url: str
    profile_github_url: str
    profile_city: str
    profile_state: str
    profile_country: str
    profile_location: str
    profile_current_company: str
    profile_current_title: str
    profile_desired_salary: str
    profile_start_date: str
    profile_work_authorized: str
    profile_visa_sponsorship: str
    profile_willing_to_relocate: str
    profile_18_or_older: str
    profile_gender: str
    profile_race_ethnicity: str
    profile_veteran_status: str
    profile_disability_status: str
    profile_citizenship: str
    profile_security_clearance: str
    profile_background_check_consent: str
    profile_drug_test_consent: str
    profile_criminal_history: str
    cover_letter: str


class BatchCreate(BaseModel):
    workspace_id: int
    query: str = "Software Engineer"
    days: int = Field(2, ge=1, le=30)
    max_jobs: int = Field(100, ge=1, le=1000)
    job_title_query: str = ""
    technology_keywords_query: str = ""
    job_description_query: str = ""
    departments: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
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
    job_title_query: str = ""
    technology_keywords_query: str = ""
    job_description_query: str = ""
    departments: list[str] = Field(default_factory=list)
    seniority: list[str] = Field(default_factory=list)
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


FieldType = Literal["text", "textarea", "select", "radio", "checkbox"]


class BlockedQuestionAnswer(BaseModel):
    answer_text: str = Field(min_length=1, max_length=2000)


class JobBlockedQuestionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    job_id: int
    question_text: str
    field_type: str
    options: list[str]
    drafted_answer: str | None
    drafted_by_model: str | None
    status: str
    answer_text: str | None
    created_at: datetime


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
