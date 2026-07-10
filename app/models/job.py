"""Job database and display models.

SQLModel classes with `table=True` map to SQLite tables. Plain SQLModel classes
without `table=True` are useful response/view shapes.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    """Jobs table from sql/hunter-agent.sql.

    TODO: Add SQLModel relationships after you are comfortable with joins.
    """

    __tablename__ = "jobs"

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profiles.id")
    title: str
    company_id: int = Field(foreign_key="companies.id")
    location_id: int = Field(foreign_key="locations.id")
    url: str | None = Field(default=None, unique=True)
    source_id: int = Field(foreign_key="sources.id")
    description: str | None = None
    hash: str | None = Field(default=None, unique=True)
    scraped_at: datetime = Field(default_factory=datetime.now)
    score: int | None = None
    score_reasoning: str | None = None


class JobListItem(SQLModel):
    """Display shape for one job card in the HTMX template."""

    id: int
    company_id: int
    title: str
    company: str
    location: str
    source: str
    score: int | None
    url: str | None
    blacklisted: bool = False
    blacklist_kind: str | None = None  # "job" or "company" when blacklisted
    blacklist_reason: str | None = None
