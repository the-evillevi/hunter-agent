"""Resume storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries for
resume profiles, sections, and items. Keeping database access here mirrors
app/services/applications.py and keeps future HTML routes easy to read.

The write helpers (`create_resume_profile`, `add_section`, `add_item`) only
flush, so multi-step writes like a resume.json import stay atomic: the caller
owns the transaction and commits once when the whole graph is in place.
"""

import json
from datetime import datetime

from sqlalchemy import distinct
from sqlmodel import Session, func, select

from app.models.resume import (
    ExportFormat,
    ResumeDetail,
    ResumeExportProfile,
    ResumeItem,
    ResumeItemDetail,
    ResumeListItem,
    ResumeProfile,
    ResumeSection,
    ResumeSectionDetail,
    SectionType,
)


def list_resumes(session: Session, limit: int = 25) -> list[ResumeListItem]:
    """Return recent resume profiles with section/item counts."""
    statement = (
        select(
            ResumeProfile.id,
            ResumeProfile.name,
            ResumeProfile.base_resume_id,
            ResumeProfile.job_id,
            func.count(distinct(ResumeSection.id)),
            func.count(ResumeItem.id),
            ResumeProfile.created_at,
        )
        .join(ResumeSection, ResumeSection.profile_id == ResumeProfile.id, isouter=True)
        .join(ResumeItem, ResumeItem.section_id == ResumeSection.id, isouter=True)
        .where(ResumeProfile.deleted_at.is_(None))
        .group_by(ResumeProfile.id)
        .order_by(ResumeProfile.created_at.desc(), ResumeProfile.id.desc())
        .limit(limit)
    )

    rows = session.exec(statement).all()
    return [
        ResumeListItem(
            id=profile_id,
            name=name,
            base_resume_id=base_resume_id,
            job_id=job_id,
            section_count=section_count,
            item_count=item_count,
            created_at=created_at,
        )
        for (
            profile_id,
            name,
            base_resume_id,
            job_id,
            section_count,
            item_count,
            created_at,
        ) in rows
    ]


def get_resume_detail(session: Session, resume_id: int) -> ResumeDetail | None:
    """Return one profile with its ordered sections and decoded items."""
    profile = session.get(ResumeProfile, resume_id)
    if profile is None or profile.deleted_at is not None:
        return None

    sections = session.exec(
        select(ResumeSection)
        .where(ResumeSection.profile_id == resume_id)
        .order_by(ResumeSection.order_idx, ResumeSection.id)
    ).all()

    # One query for every item in the profile, grouped in Python, so the
    # detail view does not issue one query per section.
    section_ids = [section.id for section in sections]
    items_by_section: dict[int, list[ResumeItem]] = {}
    if section_ids:
        items = session.exec(
            select(ResumeItem)
            .where(ResumeItem.section_id.in_(section_ids))
            .order_by(ResumeItem.order_idx, ResumeItem.id)
        ).all()
        for item in items:
            items_by_section.setdefault(item.section_id, []).append(item)

    section_details = [
        ResumeSectionDetail(
            id=section.id,
            section_type=section.section_type,
            title=section.title,
            order_idx=section.order_idx,
            items=[
                ResumeItemDetail(
                    id=item.id,
                    content=item.content_dict(),
                    relevance_score=item.relevance_score,
                    score_reasoning=item.score_reasoning,
                    order_idx=item.order_idx,
                )
                for item in items_by_section.get(section.id, [])
            ],
        )
        for section in sections
    ]

    return ResumeDetail(
        id=profile.id,
        name=profile.name,
        base_resume_id=profile.base_resume_id,
        job_id=profile.job_id,
        created_at=profile.created_at,
        sections=section_details,
    )


def create_resume_profile(
    session: Session,
    *,
    name: str,
    base_resume_id: int | None = None,
    job_id: int | None = None,
) -> ResumeProfile:
    """Stage a new empty profile; the caller commits the transaction."""
    profile = ResumeProfile(name=name, base_resume_id=base_resume_id, job_id=job_id)
    session.add(profile)
    session.flush()
    return profile


def add_section(
    session: Session,
    *,
    profile_id: int,
    section_type: SectionType,
    title: str,
    order_idx: int = 0,
) -> ResumeSection:
    """Stage one section under a profile; the caller commits."""
    section = ResumeSection(
        profile_id=profile_id,
        section_type=section_type,
        title=title,
        order_idx=order_idx,
    )
    session.add(section)
    session.flush()
    return section


def add_item(
    session: Session,
    *,
    section_id: int,
    content: dict,
    order_idx: int = 0,
) -> ResumeItem:
    """Stage one JSON fact payload under a section; the caller commits."""
    item = ResumeItem(
        section_id=section_id,
        content=json.dumps(content, ensure_ascii=False),
        order_idx=order_idx,
    )
    session.add(item)
    session.flush()
    return item


def soft_delete_profile(session: Session, *, profile_id: int) -> ResumeProfile:
    """Hide a profile from list/detail queries while keeping its audit rows.

    Cascading hard deletes are reserved for the database layer (ON DELETE);
    application code always soft-deletes so tailor history stays queryable.
    """
    profile = session.get(ResumeProfile, profile_id)
    if profile is None:
        raise LookupError(f"Resume profile {profile_id} was not found")

    profile.deleted_at = datetime.now()
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def create_export_profile(
    session: Session,
    *,
    name: str,
    format: ExportFormat,
    include_scores: bool = False,
    section_filters: list[SectionType] | None = None,
) -> ResumeExportProfile:
    """Persist a saved export configuration for the compiler layer."""
    export_profile = ResumeExportProfile(
        name=name,
        format=ExportFormat(format),
        include_scores=include_scores,
        section_filters=(
            None
            if section_filters is None
            else json.dumps([section_type.value for section_type in section_filters])
        ),
    )
    session.add(export_profile)
    session.commit()
    session.refresh(export_profile)
    return export_profile


def list_export_profiles(session: Session) -> list[ResumeExportProfile]:
    """Return saved export configurations, newest first."""
    return list(
        session.exec(
            select(ResumeExportProfile).order_by(
                ResumeExportProfile.created_at.desc(), ResumeExportProfile.id.desc()
            )
        ).all()
    )


def update_item_score(
    session: Session,
    *,
    item_id: int,
    score: float,
    reasoning: str | None = None,
) -> ResumeItem:
    """Record a tailoring score on one item and bump the profile timestamp."""
    if not 0 <= score <= 100:
        raise ValueError(f"Relevance score must be between 0 and 100, got {score}")

    item = session.get(ResumeItem, item_id)
    if item is None:
        raise LookupError(f"Resume item {item_id} was not found")

    item.relevance_score = score
    item.score_reasoning = reasoning
    session.add(item)

    section = session.get(ResumeSection, item.section_id)
    if section is not None:
        profile = session.get(ResumeProfile, section.profile_id)
        if profile is not None:
            profile.updated_at = datetime.now()
            session.add(profile)

    session.commit()
    session.refresh(item)
    return item
