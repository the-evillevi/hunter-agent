"""Service tests for resume CRUD helpers and the resume.json importer."""

from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlmodel import Session

from app.models.resume import ExportFormat, SectionType
from app.services.resume_crud import (
    add_item,
    add_section,
    create_export_profile,
    create_resume_profile,
    get_resume_detail,
    list_export_profiles,
    list_resumes,
    soft_delete_profile,
    update_item_score,
)
from app.services.resume_import import import_resume, load_resume_document


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_sample.json"


def test_list_resumes_returns_empty_for_new_database(session: Session) -> None:
    assert list_resumes(session) == []


def test_list_resumes_counts_sections_and_items(session: Session) -> None:
    profile = create_resume_profile(session, name="master")
    section = add_section(
        session,
        profile_id=profile.id,
        section_type=SectionType.experience,
        title="Work Experience",
    )
    add_item(session, section_id=section.id, content={"company": "IBM"})
    add_item(session, section_id=section.id, content={"company": "Oracle"})

    rows = list_resumes(session)

    assert len(rows) == 1
    assert rows[0].name == "master"
    assert rows[0].section_count == 1
    assert rows[0].item_count == 2


def test_get_resume_detail_returns_none_for_missing_profile(
    session: Session,
) -> None:
    assert get_resume_detail(session, resume_id=999) is None


def test_get_resume_detail_orders_sections_and_decodes_items(
    session: Session,
) -> None:
    profile = create_resume_profile(session, name="master")
    skills = add_section(
        session,
        profile_id=profile.id,
        section_type=SectionType.skills,
        title="Technical Skills",
        order_idx=1,
    )
    experience = add_section(
        session,
        profile_id=profile.id,
        section_type=SectionType.experience,
        title="Work Experience",
        order_idx=0,
    )
    add_item(session, section_id=experience.id, content={"company": "IBM"})
    add_item(session, section_id=skills.id, content={"category": "Languages"})

    detail = get_resume_detail(session, resume_id=profile.id)

    assert detail is not None
    assert [section.title for section in detail.sections] == [
        "Work Experience",
        "Technical Skills",
    ]
    assert detail.sections[0].items[0].content == {"company": "IBM"}


def test_update_item_score_persists_score_and_reasoning(session: Session) -> None:
    profile = create_resume_profile(session, name="master")
    section = add_section(
        session,
        profile_id=profile.id,
        section_type=SectionType.experience,
        title="Work Experience",
    )
    item = add_item(session, section_id=section.id, content={"company": "IBM"})

    updated = update_item_score(
        session,
        item_id=item.id,
        score=87.0,
        reasoning="Directly matches the job's data engineering focus.",
    )

    assert updated.relevance_score == 87.0
    assert updated.score_reasoning is not None


def test_update_item_score_raises_for_missing_item(session: Session) -> None:
    with pytest.raises(LookupError):
        update_item_score(session, item_id=999, score=50.0)


def test_update_item_score_rejects_out_of_range_score(session: Session) -> None:
    """Validate in Python so a bad score cannot poison the session with an
    IntegrityError from the database CHECK constraint."""
    profile = create_resume_profile(session, name="master")
    section = add_section(
        session,
        profile_id=profile.id,
        section_type=SectionType.experience,
        title="Work Experience",
    )
    item = add_item(session, section_id=section.id, content={"company": "IBM"})
    session.commit()

    with pytest.raises(ValueError):
        update_item_score(session, item_id=item.id, score=150.0)


def test_soft_deleted_profile_is_hidden_from_list_and_detail(
    session: Session,
) -> None:
    profile = create_resume_profile(session, name="master")
    session.commit()

    deleted = soft_delete_profile(session, profile_id=profile.id)

    assert deleted.deleted_at is not None
    assert list_resumes(session) == []
    assert get_resume_detail(session, resume_id=profile.id) is None


def test_soft_delete_profile_raises_for_missing_profile(session: Session) -> None:
    with pytest.raises(LookupError):
        soft_delete_profile(session, profile_id=999)


def test_export_profile_crud_round_trip(session: Session) -> None:
    create_export_profile(
        session,
        name="pdf-core",
        format=ExportFormat.pdf,
        include_scores=True,
        section_filters=[SectionType.experience, SectionType.skills],
    )
    create_export_profile(session, name="full-json", format=ExportFormat.json)

    export_profiles = list_export_profiles(session)

    assert [profile.name for profile in export_profiles] == [
        "full-json",
        "pdf-core",
    ]
    assert export_profiles[1].section_filters_list() == [
        SectionType.experience,
        SectionType.skills,
    ]
    assert export_profiles[0].section_filters_list() is None


def test_load_resume_document_validates_fixture() -> None:
    document = load_resume_document(FIXTURE_PATH)

    assert document.name == "sample"
    assert document.basics["name"] == "Sample Person"
    assert [section.type for section in document.sections] == [
        SectionType.summary,
        SectionType.experience,
        SectionType.skills,
        SectionType.education,
    ]


def test_load_resume_document_rejects_basics_inside_sections(tmp_path) -> None:
    """Contact info belongs in the top-level basics key, never as a section."""
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(
        '{"name": "bad", "basics": {}, "sections":'
        ' [{"type": "basics", "title": "Contact", "items": []}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_resume_document(bad_path)


def test_import_resume_rolls_back_partial_graph_on_failure(
    session: Session, monkeypatch
) -> None:
    """A failure part-way through an import must not leave orphan profiles."""
    import app.services.resume_import as resume_import_module

    document = load_resume_document(FIXTURE_PATH)

    real_add_item = resume_import_module.add_item
    calls = {"count": 0}

    def failing_add_item(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] >= 2:
            raise RuntimeError("simulated mid-import failure")
        return real_add_item(*args, **kwargs)

    monkeypatch.setattr(resume_import_module, "add_item", failing_add_item)

    with pytest.raises(RuntimeError):
        import_resume(session, document)

    assert list_resumes(session) == []


def test_load_resume_document_rejects_unknown_section_type(tmp_path) -> None:
    bad_path = tmp_path / "bad.json"
    bad_path.write_text(
        '{"name": "bad", "basics": {}, "sections":'
        ' [{"type": "hobbies", "title": "Hobbies", "items": []}]}',
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_resume_document(bad_path)


def test_import_resume_creates_full_profile_graph(session: Session) -> None:
    document = load_resume_document(FIXTURE_PATH)

    profile = import_resume(session, document)
    detail = get_resume_detail(session, resume_id=profile.id)

    assert detail is not None
    assert detail.name == "sample"
    # basics section first, then the four document sections in file order.
    assert [section.section_type for section in detail.sections] == [
        SectionType.basics,
        SectionType.summary,
        SectionType.experience,
        SectionType.skills,
        SectionType.education,
    ]
    assert detail.sections[0].items[0].content["email"] == "sample@example.test"

    experience = detail.sections[2]
    assert [item.content["company"] for item in experience.items] == ["IBM", "Oracle"]


def test_import_master_resume_json_from_repo(session: Session) -> None:
    """The committed cvs/resume.json must always import cleanly."""
    document = load_resume_document()

    profile = import_resume(session, document)
    detail = get_resume_detail(session, resume_id=profile.id)

    assert detail is not None
    assert detail.name == "master"
    section_types = {section.section_type for section in detail.sections}
    assert SectionType.experience in section_types
    assert SectionType.skills in section_types
