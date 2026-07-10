"""Tests for the resume tailoring service and the Ollama scoring client.

Every test uses a fake scoring client or an unreachable local address; no
test talks to a real model or leaves the machine.
"""

from pathlib import Path

import pytest
from sqlmodel import Session, select

from app.models.config import OllamaConfig
from app.models.resume import ResumeTailorRun, ResumeTailorRunItem, SectionType
from app.services.ollama_client import (
    FALLBACK_SCORE,
    OllamaClient,
    ScoringResult,
    load_scoring_prompt,
)
from app.services.resume_import import import_resume, load_resume_document
from app.services.resume_tailor import ResumeTailor
from app.services.resume_crud import get_resume_detail, list_resumes


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_sample.json"


class FakeScoringClient:
    """Deterministic stand-in for OllamaClient.

    Scores 90 for facts mentioning IBM, 20 for everything else, and records
    every scored payload so tests can assert what was (not) sent to the model.
    """

    model_name = "fake-model"
    prompt_version = "test"

    def __init__(self) -> None:
        self.scored_contents: list[str] = []

    def score_item(
        self, *, item_content: str, job_title: str, job_description: str
    ) -> ScoringResult:
        self.scored_contents.append(item_content)
        if "IBM" in item_content:
            return ScoringResult(score=90, reasoning="Directly relevant.")
        return ScoringResult(score=20, reasoning="Unrelated to this job.")


@pytest.fixture()
def base_resume_id(session: Session) -> int:
    document = load_resume_document(FIXTURE_PATH)
    return import_resume(session, document).id


def test_tailor_filters_low_scoring_items(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )
    detail = get_resume_detail(session, variant.id)

    experience_sections = [
        section
        for section in detail.sections
        if section.section_type == SectionType.experience
    ]
    assert len(experience_sections) == 1
    companies = [item.content["company"] for item in experience_sections[0].items]
    assert companies == ["IBM"]  # the Oracle item scored 20 and was dropped

    kept_item = experience_sections[0].items[0]
    assert kept_item.relevance_score == 90
    assert kept_item.score_reasoning == "Directly relevant."


def test_tailor_drops_sections_with_no_surviving_items(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )
    detail = get_resume_detail(session, variant.id)

    section_types = {section.section_type for section in detail.sections}
    # summary, skills, and education fixture items all score 20 (< 40).
    assert SectionType.summary not in section_types
    assert SectionType.skills not in section_types
    assert SectionType.education not in section_types


def test_tailor_copies_basics_without_scoring(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    fake_client = FakeScoringClient()
    tailor = ResumeTailor(client=fake_client)

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )
    detail = get_resume_detail(session, variant.id)

    basics_sections = [
        section
        for section in detail.sections
        if section.section_type == SectionType.basics
    ]
    assert len(basics_sections) == 1
    basics_item = basics_sections[0].items[0]
    assert basics_item.content["email"] == "sample@example.test"
    assert basics_item.relevance_score is None
    assert all(
        "sample@example.test" not in sent for sent in fake_client.scored_contents
    )


def test_tailor_records_audit_run(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )

    runs = session.exec(select(ResumeTailorRun)).all()
    assert len(runs) == 1
    assert runs[0].source_profile_id == base_resume_id
    assert runs[0].output_profile_id == variant.id
    assert runs[0].job_id == job.id
    assert runs[0].model == "fake-model"
    assert runs[0].prompt_version == "test"
    assert runs[0].duration_ms >= 0


def test_tailor_records_dropped_items_in_run_audit(
    session: Session, create_job, base_resume_id: int
) -> None:
    """Filtered-out items keep their scores in the audit for analytics."""
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    tailor.tailor_to_job(session, base_resume_id=base_resume_id, job_id=job.id)

    run = session.exec(select(ResumeTailorRun)).one()
    audit_items = session.exec(
        select(ResumeTailorRunItem).where(ResumeTailorRunItem.run_id == run.id)
    ).all()

    # Every scorable item in the fixture is audited, kept or not; basics
    # is copied verbatim and never audited.
    assert audit_items
    assert all(item.section_type != SectionType.basics for item in audit_items)

    dropped = [item for item in audit_items if not item.kept]
    assert dropped
    assert all(item.score == 20 for item in dropped)
    assert all(item.reasoning == "Unrelated to this job." for item in dropped)

    kept = [item for item in audit_items if item.kept]
    assert kept
    assert all("IBM" in item.item_content for item in kept)


def test_tailor_leaves_base_resume_untouched(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    before = get_resume_detail(session, base_resume_id)
    tailor = ResumeTailor(client=FakeScoringClient())

    tailor.tailor_to_job(session, base_resume_id=base_resume_id, job_id=job.id)
    after = get_resume_detail(session, base_resume_id)

    assert after == before


def test_tailor_variant_links_base_and_job(
    session: Session, create_job, base_resume_id: int
) -> None:
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )

    assert variant.base_resume_id == base_resume_id
    assert variant.job_id == job.id
    assert variant.name.startswith("tailored-data-engineer-")


def test_tailor_raises_for_missing_resume(session: Session, create_job) -> None:
    job = create_job()
    tailor = ResumeTailor(client=FakeScoringClient())

    with pytest.raises(LookupError):
        tailor.tailor_to_job(session, base_resume_id=999, job_id=job.id)


def test_tailor_raises_for_missing_job(session: Session, base_resume_id: int) -> None:
    tailor = ResumeTailor(client=FakeScoringClient())

    with pytest.raises(LookupError):
        tailor.tailor_to_job(session, base_resume_id=base_resume_id, job_id=999)

    # The failed run must not leave a half-written variant behind.
    assert len(list_resumes(session)) == 1


def test_tailor_rolls_back_variant_on_mid_write_failure(
    session: Session, create_job, base_resume_id: int, monkeypatch
) -> None:
    """A failure after the variant is staged must roll everything back."""
    import app.services.resume_tailor as resume_tailor_module

    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FakeScoringClient())

    def failing_add_item(*args, **kwargs):
        raise RuntimeError("simulated mid-write failure")

    monkeypatch.setattr(resume_tailor_module, "add_item", failing_add_item)

    with pytest.raises(RuntimeError):
        tailor.tailor_to_job(session, base_resume_id=base_resume_id, job_id=job.id)

    assert len(list_resumes(session)) == 1  # only the imported base remains


class FallbackScoringClient:
    """Simulates the model server being down: every score is a fallback."""

    model_name = "fake-model"
    prompt_version = "test"

    def score_item(
        self, *, item_content: str, job_title: str, job_description: str
    ) -> ScoringResult:
        return ScoringResult(
            score=FALLBACK_SCORE,
            reasoning="Ollama unavailable; assigned neutral fallback score",
            is_fallback=True,
        )


def test_tailor_persists_fallback_flag_on_items(
    session: Session, create_job, base_resume_id: int
) -> None:
    """Fallback 50s must stay distinguishable from real model scores."""
    job = create_job(title="Data Engineer")
    tailor = ResumeTailor(client=FallbackScoringClient())

    variant = tailor.tailor_to_job(
        session, base_resume_id=base_resume_id, job_id=job.id
    )
    detail = get_resume_detail(session, variant.id)

    scored_items = [
        item
        for section in detail.sections
        if section.section_type != SectionType.basics
        for item in section.items
    ]
    assert scored_items  # fallback score 50 passes the default threshold
    assert all(item.score_is_fallback is True for item in scored_items)
    assert all(item.relevance_score == FALLBACK_SCORE for item in scored_items)

    basics_items = [
        item
        for section in detail.sections
        if section.section_type == SectionType.basics
        for item in section.items
    ]
    assert all(item.score_is_fallback is False for item in basics_items)


def _unreachable_ollama_config() -> OllamaConfig:
    return OllamaConfig.model_validate(
        {
            "base_url": "http://127.0.0.1:9",
            "scorer": {"model": "qwen2.5:7b", "temperature": 0.1, "max_tokens": 512},
            "tailor": {"model": "qwen2.5:14b", "temperature": 0.3, "max_tokens": 2048},
        }
    )


def test_ollama_client_falls_back_when_server_unreachable() -> None:
    client = OllamaClient(_unreachable_ollama_config(), timeout=0.2)

    result = client.score_item(
        item_content="{'company': 'IBM'}",
        job_title="Data Engineer",
        job_description="ETL pipelines",
    )

    assert result.is_fallback is True
    assert result.score == FALLBACK_SCORE


def test_ollama_client_rejects_malformed_model_output() -> None:
    client = OllamaClient(_unreachable_ollama_config(), timeout=0.2)

    for bad_output in [
        "not json",
        '{"reasoning": "no score"}',
        '{"score": 150, "reasoning": "x"}',
    ]:
        result = client._parse_model_output(bad_output)
        assert result.is_fallback is True
        assert result.score == FALLBACK_SCORE


def test_scoring_prompt_declares_version_and_placeholders() -> None:
    version, template = load_scoring_prompt()

    assert version == "v1"
    for placeholder in ("{item_content}", "{job_title}", "{job_description}"):
        assert placeholder in template
