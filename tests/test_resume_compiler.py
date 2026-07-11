"""Tests for the resume compiler's four export formats."""

import sys
from pathlib import Path

import pytest
from sqlmodel import Session

from app.models.resume import SectionType
from app.services.resume_compiler import (
    MAX_PDF_BYTES,
    ResumeCompiler,
    ResumeExportError,
)
from app.services.resume_crud import get_resume_detail, update_item_score
from app.services.resume_import import import_resume, load_resume_document


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_sample.json"


@pytest.fixture()
def detail(session: Session):
    document = load_resume_document(FIXTURE_PATH)
    profile = import_resume(session, document)
    return get_resume_detail(session, profile.id)


@pytest.fixture()
def compiler() -> ResumeCompiler:
    return ResumeCompiler()


def test_to_json_includes_all_sections_and_scores(compiler, detail) -> None:
    payload = compiler.to_json(detail)

    assert payload["name"] == "sample"
    section_types = [section["section_type"] for section in payload["sections"]]
    assert section_types == [
        "basics",
        "summary",
        "experience",
        "skills",
        "education",
    ]
    experience_section = next(
        section
        for section in payload["sections"]
        if section["section_type"] == "experience"
    )
    experience_items = experience_section["items"]
    assert experience_items[0]["content"]["company"] == "IBM"
    assert "relevance_score" in experience_items[0]


def test_to_json_section_filter_keeps_basics(compiler, detail) -> None:
    payload = compiler.to_json(detail, sections={SectionType.experience})

    section_types = [section["section_type"] for section in payload["sections"]]
    assert section_types == ["basics", "experience"]


def test_to_json_resume_maps_standard_keys(compiler, detail) -> None:
    document = compiler.to_json_resume(detail)

    assert document["basics"]["name"] == "Sample Person"
    assert document["basics"]["email"] == "sample@example.test"
    assert document["basics"]["summary"] == "Engineer with test-fixture experience."

    assert [work["name"] for work in document["work"]] == ["IBM", "Oracle"]
    assert document["work"][0]["position"] == "Application Developer"
    assert document["work"][0]["startDate"] == "2023-10"
    # Ongoing role: the schema requires iso8601 endDate values when the key
    # is present, so an open-ended job must omit it entirely.
    assert "endDate" not in document["work"][0]

    assert document["skills"][0]["name"] == "Languages"
    assert "Python" in document["skills"][0]["keywords"]

    assert document["education"][0]["institution"] == "Tecnológico de Monterrey"
    assert document["education"][0]["studyType"].startswith("Bachelor's Degree")


def test_to_json_resume_is_json_serializable(compiler, detail) -> None:
    import json

    document = compiler.to_json_resume(detail)
    assert json.loads(json.dumps(document)) == document


def test_to_html_renders_standalone_document(compiler, detail) -> None:
    html = compiler.to_html(detail)

    assert html.strip().startswith("<!doctype html>")
    assert "Sample Person" in html
    assert "IBM" in html
    assert "Designed and implemented automated ETL workflows." in html
    assert "cdn" not in html.lower()  # standalone: no external resources


def _section_of_type(detail, wanted: SectionType):
    return next(
        section for section in detail.sections if section.section_type == wanted
    )


def test_to_html_hides_scores_by_default(compiler, detail, session) -> None:
    experience_item_id = _section_of_type(detail, SectionType.experience).items[0].id
    update_item_score(session, item_id=experience_item_id, score=90.0)
    scored_detail = get_resume_detail(session, detail.id)

    plain_html = compiler.to_html(scored_detail)
    scored_html = compiler.to_html(scored_detail, include_scores=True)

    assert 'class="score"' not in plain_html
    assert 'class="score"' in scored_html
    assert ">90<" in scored_html


def test_to_html_section_filter(compiler, detail) -> None:
    html = compiler.to_html(detail, sections={SectionType.skills})

    assert "Technical Skills" in html
    assert "Work Experience" not in html
    assert "Sample Person" in html  # basics always renders


def test_to_pdf_produces_valid_pdf(compiler, detail) -> None:
    try:
        pdf_bytes = compiler.to_pdf(detail)
    except ResumeExportError as error:
        pytest.skip(f"PDF native dependencies unavailable: {error}")

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) < MAX_PDF_BYTES


def test_json_resume_mapper_covers_every_section_type() -> None:
    """A new SectionType must get an explicit mapping decision, not a silent
    drop from the JSON Resume export."""
    from app.services.resume_compiler import _JSON_RESUME_SECTION_MAPPERS

    assert set(_JSON_RESUME_SECTION_MAPPERS) == set(SectionType)


def test_to_json_resume_keeps_summary_when_basics_maps_last(compiler, detail) -> None:
    """_map_basics must merge into basics, not clobber an earlier summary."""
    reordered = detail.model_copy(deep=True)
    reordered.sections.sort(
        key=lambda section: section.section_type == SectionType.basics
    )
    assert reordered.sections[-1].section_type == SectionType.basics

    document = compiler.to_json_resume(reordered)

    assert document["basics"]["summary"] == "Engineer with test-fixture experience."
    assert document["basics"]["name"] == "Sample Person"


def test_to_pdf_raises_clear_error_when_native_libraries_break(
    compiler, detail, monkeypatch
) -> None:
    """weasyprint importable but Pango/GObject missing surfaces as our error."""

    class BrokenWeasyprint:
        @staticmethod
        def HTML(string: str):
            raise OSError("cannot load library 'gobject-2.0-0'")

    monkeypatch.setitem(sys.modules, "weasyprint", BrokenWeasyprint())

    with pytest.raises(ResumeExportError, match="native libraries"):
        compiler.to_pdf(detail)


def test_to_pdf_raises_clear_error_without_weasyprint(
    compiler, detail, monkeypatch
) -> None:
    """The app must run (and fail helpfully) when weasyprint is absent."""
    monkeypatch.setitem(sys.modules, "weasyprint", None)

    with pytest.raises(ResumeExportError, match="weasyprint"):
        compiler.to_pdf(detail)


def test_to_json_resume_rejects_spec_violations(compiler, detail) -> None:
    """A mapping that breaks the v1.0.0 schema must fail, not export."""
    # An ongoing role encoded as end_date="" used to slip through as an
    # empty endDate, which the schema's iso8601 pattern rejects.
    experience = _section_of_type(detail, SectionType.experience)
    experience.items[0].content["end_date"] = "not-a-date"

    with pytest.raises(ResumeExportError, match="schema validation"):
        compiler.to_json_resume(detail)


def _hundred_item_detail(detail):
    """Inflate the fixture resume to 100+ experience items for stress tests."""
    inflated = detail.model_copy(deep=True)
    experience = _section_of_type(inflated, SectionType.experience)
    template_item = experience.items[0]
    for index in range(110):
        clone = template_item.model_copy(deep=True)
        clone.id = 1000 + index
        clone.order_idx = index + len(experience.items)
        clone.content = dict(template_item.content) | {
            "position": f"Engineer {index}",
            "highlights": [f"Delivered project {index} end to end."],
        }
        experience.items.append(clone)
    return inflated


def test_to_pdf_handles_hundred_plus_items_within_size_limit(compiler, detail) -> None:
    inflated = _hundred_item_detail(detail)

    try:
        pdf_bytes = compiler.to_pdf(inflated)
    except ResumeExportError as error:
        pytest.skip(f"PDF native dependencies unavailable: {error}")

    assert pdf_bytes.startswith(b"%PDF")
    assert len(pdf_bytes) < MAX_PDF_BYTES
