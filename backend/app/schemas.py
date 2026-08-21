from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import JobStatus


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


SortField = Literal["score", "date"]
SortOrder = Literal["asc", "desc"]
