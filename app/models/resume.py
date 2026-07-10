"""Resume database and display models.

The resume architecture stores structured CV facts instead of opaque file
paths. A ResumeProfile is one resume version (the master imported from
cvs/resume.json, or a variant tailored for one job). Profiles own ordered
sections, and sections own ordered items whose flexible payload lives in a
JSON-encoded ``content`` column so new fact shapes never require migrations.
"""

import json
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer
from sqlmodel import Field, SQLModel


class SectionType(StrEnum):
    # `basics` holds contact info and availability as one item. It must be
    # excluded from relevance scoring and item filtering in the tailor layer:
    # a resume without a name or email is never the right output.
    basics = "basics"
    summary = "summary"
    experience = "experience"
    education = "education"
    skills = "skills"
    projects = "projects"
    certifications = "certifications"


# Built from the enum so adding a section type only requires touching
# SectionType here and the CHECK constraint in sql/hunter-agent.sql.
_SECTION_TYPE_SQL_LIST = ", ".join(f"'{section_type}'" for section_type in SectionType)


class ExportFormat(StrEnum):
    json = "json"
    json_resume = "json_resume"
    html = "html"
    pdf = "pdf"


_EXPORT_FORMAT_SQL_LIST = ", ".join(f"'{fmt}'" for fmt in ExportFormat)


class ResumeProfile(SQLModel, table=True):
    """Resume profiles table from sql/hunter-agent.sql.

    ``base_resume_id`` is NULL for master resumes and points at the source
    profile for tailored variants. ``job_id`` is set only on variants that
    were tailored for one specific job.

    ``deleted_at`` implements soft deletes: profiles keep their audit trail
    but disappear from list/detail queries once the timestamp is set. Deleting
    a base resume must not destroy its variants, so the self-reference nulls
    out instead of cascading.
    """

    __tablename__ = "resume_profiles"

    id: int | None = Field(default=None, primary_key=True)
    name: str
    base_resume_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("resume_profiles.id", ondelete="SET NULL")
        ),
    )
    job_id: int | None = Field(default=None, foreign_key="jobs.id")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    deleted_at: datetime | None = None


class ResumeSection(SQLModel, table=True):
    """Resume sections table: one titled, ordered block inside a profile."""

    __tablename__ = "resume_sections"
    __table_args__ = (
        CheckConstraint(
            f"section_type IN ({_SECTION_TYPE_SQL_LIST})",
            name="ck_resume_sections_section_type",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    profile_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("resume_profiles.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    section_type: SectionType
    title: str
    order_idx: int = 0


class ResumeItem(SQLModel, table=True):
    """Resume items table: one fact (a job, a skill group, a degree).

    ``content`` stores the JSON-encoded fact payload. ``relevance_score`` and
    ``score_reasoning`` stay NULL until a tailoring run scores the item.
    """

    __tablename__ = "resume_items"
    __table_args__ = (
        CheckConstraint(
            "relevance_score IS NULL OR relevance_score BETWEEN 0 AND 100",
            name="ck_resume_items_relevance_score",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    section_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("resume_sections.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    content: str
    relevance_score: float | None = None
    score_reasoning: str | None = None
    order_idx: int = 0

    def content_dict(self) -> dict:
        """Decode the JSON payload for display and export layers."""
        return json.loads(self.content)


class ResumeTailorRun(SQLModel, table=True):
    """Audit log: one tailoring run that produced a variant for a job."""

    __tablename__ = "resume_tailor_runs"

    id: int | None = Field(default=None, primary_key=True)
    source_profile_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("resume_profiles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    output_profile_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("resume_profiles.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    job_id: int = Field(foreign_key="jobs.id")
    model: str
    prompt_version: str
    created_at: datetime = Field(default_factory=datetime.now)


class ResumeExportProfile(SQLModel, table=True):
    """Saved export configuration: which format and sections to compile.

    ``section_filters`` stores a JSON-encoded list of section type names, or
    NULL to export every section.
    """

    __tablename__ = "resume_export_profiles"
    __table_args__ = (
        CheckConstraint(
            f"format IN ({_EXPORT_FORMAT_SQL_LIST})",
            name="ck_resume_export_profiles_format",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str
    format: ExportFormat
    include_scores: bool = False
    section_filters: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)

    def section_filters_list(self) -> list[SectionType] | None:
        """Decode the JSON filter payload for the compiler layer."""
        if self.section_filters is None:
            return None
        return [SectionType(name) for name in json.loads(self.section_filters)]


class ResumeListItem(SQLModel):
    """Display shape for one resume row in the HTMX list template."""

    id: int
    name: str
    base_resume_id: int | None
    job_id: int | None
    section_count: int
    item_count: int
    created_at: datetime


class ResumeItemDetail(SQLModel):
    """Display shape for one decoded item inside a section."""

    id: int
    content: dict
    relevance_score: float | None
    score_reasoning: str | None
    order_idx: int


class ResumeSectionDetail(SQLModel):
    """Display shape for one section with its ordered items."""

    id: int
    section_type: SectionType
    title: str
    order_idx: int
    items: list[ResumeItemDetail]


class ResumeDetail(SQLModel):
    """Display shape for a full resume: profile plus nested sections."""

    id: int
    name: str
    base_resume_id: int | None
    job_id: int | None
    created_at: datetime
    sections: list[ResumeSectionDetail]
