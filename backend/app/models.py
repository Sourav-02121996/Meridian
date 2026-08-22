import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Float, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, enum.Enum):
    discovered = "discovered"
    to_apply = "to_apply"
    applied = "applied"
    skipped = "skipped"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
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
    date_fetched: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    date_scored: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    date_applied: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
