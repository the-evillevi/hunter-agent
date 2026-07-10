"""Persisted job profile models and provider-query validation."""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field as PydanticField, field_validator
from sqlalchemy import CheckConstraint, Column, String
from sqlmodel import Field, SQLModel


class LocationType(StrEnum):
    """Work arrangements a profile can target; one profile may pick several."""

    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"


class KeywordKind(StrEnum):
    """Whether a profile keyword should attract or filter out jobs."""

    include = "include"
    exclude = "exclude"


class RemotiveCategory(StrEnum):
    """The subset of Remotive job categories this project searches."""

    software_development = "software-development"
    artificial_intelligence = "artificial-intelligence"
    research = "research"
    data = "data"
    engineering = "engineering"
    information_technology = "information-technology"


class RemotiveProfileQuery(BaseModel):
    """Versioned profile query understood by the future Remotive adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    category: RemotiveCategory | None = None
    company_id: int | None = PydanticField(default=None, gt=0)
    search: str | None = None
    limit: int = PydanticField(default=10, ge=1, le=10)

    @field_validator("search")
    @classmethod
    def search_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("search must not be blank")
        return value


class AdzunaProfileQuery(BaseModel):
    """Versioned profile query understood by the Adzuna adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    what: str
    where: str | None = None
    category: str | None = None
    full_time: bool | None = None
    permanent: bool | None = None

    @field_validator("what", "where", "category")
    @classmethod
    def text_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("text must not be blank")
        return value


class Profile(SQLModel, table=True):
    """Database-owned target role and matching thresholds."""

    __tablename__ = "profiles"
    __table_args__ = (
        CheckConstraint("salary_min >= 0", name="ck_profiles_salary_min"),
        CheckConstraint(
            "match_threshold BETWEEN 1 AND 100",
            name="ck_profiles_match_threshold",
        ),
        CheckConstraint("active IN (0, 1)", name="ck_profiles_active"),
    )

    id: int | None = Field(default=None, primary_key=True)
    role_name: str = Field(
        sa_column=Column(String(collation="NOCASE"), nullable=False, unique=True)
    )
    salary_min: int = Field(default=0)
    match_threshold: int = Field(default=80)
    active: bool = Field(default=True, sa_column_kwargs={"server_default": "1"})
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)


class Keyword(SQLModel, table=True):
    """Case-insensitively reusable profile keyword."""

    __tablename__ = "keywords"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(
        sa_column=Column(String(collation="NOCASE"), nullable=False, unique=True)
    )


class ProfileKeyword(SQLModel, table=True):
    """Classify one keyword as included or excluded for a profile."""

    __tablename__ = "profile_keywords"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('include', 'exclude')",
            name="ck_profile_keywords_kind",
        ),
    )

    profile_id: int = Field(foreign_key="profiles.id", primary_key=True)
    keyword_id: int = Field(foreign_key="keywords.id", primary_key=True)
    kind: KeywordKind


class ProfileLocationType(SQLModel, table=True):
    """Allow one profile to target more than one work arrangement."""

    __tablename__ = "profile_location_types"
    __table_args__ = (
        CheckConstraint(
            "location_type IN ('remote', 'hybrid', 'onsite')",
            name="ck_profile_location_types_value",
        ),
    )

    profile_id: int = Field(foreign_key="profiles.id", primary_key=True)
    location_type: LocationType = Field(primary_key=True)


class ProfileSourceQuery(SQLModel, table=True):
    """One independently editable, source-specific versioned query."""

    __tablename__ = "profile_source_queries"
    __table_args__ = (
        CheckConstraint("json_valid(query_json)", name="ck_profile_query_json"),
    )

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(foreign_key="profiles.id", index=True)
    source_id: int = Field(foreign_key="sources.id", index=True)
    query_json: str
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
