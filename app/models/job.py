"""Job database and display models.

SQLModel classes with `table=True` map to SQLite tables. Plain SQLModel classes
without `table=True` are useful response/view shapes.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    """Company table from sql/hunter-agent.sql."""

    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class Location(SQLModel, table=True):
    """Location table from sql/hunter-agent.sql."""

    __tablename__ = "locations"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class Source(SQLModel, table=True):
    """Job source table from sql/hunter-agent.sql."""

    __tablename__ = "sources"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)


class Profile(SQLModel, table=True):
    """Minimal profile table model needed by JobRecord's foreign key.

    TODO: Expand this when you build profile management screens.
    """

    __tablename__ = "profiles"

    id: int | None = Field(default=None, primary_key=True)
    role_name: str | None = None
    salary_min: int | None = None
    location_type: str | None = None
    match_threshold: int | None = None
    active: bool


class JobRecord(SQLModel, table=True):
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
    title: str
    company: str
    location: str
    source: str
    score: int | None
    url: str | None
