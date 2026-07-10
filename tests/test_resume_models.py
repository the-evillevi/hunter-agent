"""Constraint and shape tests for the resume table models."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, text

from app.models.resume import (
    ExportFormat,
    ResumeExportProfile,
    ResumeItem,
    ResumeProfile,
    ResumeSection,
    ResumeTailorRun,
    SectionType,
)
from app.services.resume_crud import add_item, add_section, create_resume_profile


def _add_section(session: Session, profile_id: int) -> ResumeSection:
    return add_section(
        session,
        profile_id=profile_id,
        section_type=SectionType.experience,
        title="Work Experience",
    )


def test_profile_section_item_round_trip(session: Session) -> None:
    profile = create_resume_profile(session, name="master")
    section = _add_section(session, profile.id)

    content = {"position": "Application Developer", "company": "IBM"}
    item = add_item(session, section_id=section.id, content=content)
    session.commit()
    session.refresh(item)

    assert item.content_dict() == content
    assert item.relevance_score is None
    assert item.score_reasoning is None


def test_section_requires_existing_profile(session: Session) -> None:
    session.add(
        ResumeSection(
            profile_id=999,
            section_type=SectionType.skills,
            title="Technical Skills",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_section_rejects_unknown_section_type(session: Session) -> None:
    profile = create_resume_profile(session, name="master")

    with pytest.raises(IntegrityError):
        session.exec(
            text(
                "INSERT INTO resume_sections (profile_id, section_type, title)"
                " VALUES (:profile_id, 'hobbies', 'Hobbies')"
            ).bindparams(profile_id=profile.id)
        )


def test_item_rejects_out_of_range_relevance_score(session: Session) -> None:
    profile = create_resume_profile(session, name="master")
    section = _add_section(session, profile.id)
    session.add(
        ResumeItem(
            section_id=section.id,
            content="{}",
            relevance_score=150.0,
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_variant_profile_links_base_and_job(session: Session, create_job) -> None:
    job = create_job()
    base = create_resume_profile(session, name="master")

    variant = ResumeProfile(
        name="tailored-ai-ml-engineer",
        base_resume_id=base.id,
        job_id=job.id,
    )
    session.add(variant)
    session.commit()
    session.refresh(variant)

    assert variant.base_resume_id == base.id
    assert variant.job_id == job.id


def test_tailor_run_requires_existing_job(session: Session) -> None:
    base = create_resume_profile(session, name="master")
    output = create_resume_profile(session, name="tailored")

    session.add(
        ResumeTailorRun(
            source_profile_id=base.id,
            output_profile_id=output.id,
            job_id=999,
            model="qwen2.5:7b",
            prompt_version="v1",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_deleting_profile_cascades_to_sections_and_items(session: Session) -> None:
    profile = create_resume_profile(session, name="master")
    section = _add_section(session, profile.id)
    item = add_item(session, section_id=section.id, content={"company": "IBM"})
    session.commit()
    section_id, item_id = section.id, item.id

    session.delete(session.get(ResumeProfile, profile.id))
    session.commit()

    assert session.get(ResumeSection, section_id) is None
    assert session.get(ResumeItem, item_id) is None


def test_deleting_profile_cascades_to_tailor_runs(
    session: Session, create_job
) -> None:
    job = create_job()
    base = create_resume_profile(session, name="master")
    output = create_resume_profile(session, name="tailored")
    run = ResumeTailorRun(
        source_profile_id=base.id,
        output_profile_id=output.id,
        job_id=job.id,
        model="qwen2.5:7b",
        prompt_version="v1",
    )
    session.add(run)
    session.commit()
    run_id = run.id

    session.delete(session.get(ResumeProfile, output.id))
    session.commit()

    assert session.get(ResumeTailorRun, run_id) is None


def test_deleting_base_profile_nulls_variant_reference(session: Session) -> None:
    base = create_resume_profile(session, name="master")
    variant = create_resume_profile(session, name="tailored", base_resume_id=base.id)
    session.commit()
    variant_id = variant.id

    session.delete(session.get(ResumeProfile, base.id))
    session.commit()
    session.expire_all()

    surviving_variant = session.get(ResumeProfile, variant_id)
    assert surviving_variant is not None
    assert surviving_variant.base_resume_id is None


def test_export_profile_round_trip(session: Session) -> None:
    export_profile = ResumeExportProfile(
        name="pdf-no-education",
        format=ExportFormat.pdf,
        include_scores=True,
        section_filters='["experience", "skills"]',
    )
    session.add(export_profile)
    session.commit()
    session.refresh(export_profile)

    assert export_profile.section_filters_list() == [
        SectionType.experience,
        SectionType.skills,
    ]


def test_export_profile_rejects_unknown_format(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.exec(
            text(
                "INSERT INTO resume_export_profiles (name, format, include_scores)"
                " VALUES ('bad', 'docx', 0)"
            )
        )
