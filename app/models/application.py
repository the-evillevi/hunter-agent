"""Application database and display models."""

from datetime import datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


class ApplicationStatus(StrEnum):
    pending = "pending"
    draft = "draft"
    applied = "applied"
    acknowledged = "acknowledged"
    interviews = "interviews"
    rejected = "rejected"
    ghosted = "ghosted"
    offer = "offer"
    accepted = "accepted"


class Application(SQLModel, table=True):
    """Applications table from sql/hunter-agent.sql.

    TODO: Add SQLModel relationships after you are comfortable with joins.
    """

    __tablename__ = "applications"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", unique=True)
    cv_path: str | None = None
    status: ApplicationStatus = ApplicationStatus.pending
    applied_at: datetime | None = None
    last_updated: datetime = Field(default_factory=datetime.now)
    notes: str | None = None


class ApplicationListItem(SQLModel):
    """Display shape for one application card in the HTMX template.

    ``resume_id``/``resume_name`` point at the newest tailored resume for
    the application's job, or stay None when the job was never tailored.
    """

    id: int
    job_id: int
    company_id: int
    job_title: str
    company: str
    cv_path: str | None
    status: ApplicationStatus
    applied_at: datetime | None
    last_updated: datetime
    notes: str | None
    resume_id: int | None = None
    resume_name: str | None = None
    blacklisted: bool = False
    blacklist_kind: str | None = None  # "job" or "company" when blacklisted
    blacklist_reason: str | None = None
