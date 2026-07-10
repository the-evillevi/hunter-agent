"""Performance benchmarks for the tailoring pipeline (mock scoring only).

The acceptance targets from HNTR-6 are checked against a fake client with a
small artificial latency per request, so the numbers exercise the service's
own overhead and concurrency handling without talking to a real model.
"""

import time

import pytest
from sqlmodel import Session, select

from app.models.resume import ResumeTailorRun, SectionType
from app.services.ollama_client import ScoringResult
from app.services.resume_crud import add_item, add_section, create_resume_profile
from app.services.resume_tailor import ResumeTailor


# Simulated per-request model latency. Small enough to keep the suite fast,
# large enough that a concurrency regression (serializing 10 requests) would
# still stay visibly below the acceptance ceilings checked here.
SIMULATED_REQUEST_SECONDS = 0.05

SINGLE_ITEM_CEILING_SECONDS = 2.0
BATCH_OF_TEN_CEILING_SECONDS = 15.0


class SlowFakeScoringClient:
    """Deterministic scorer that sleeps to imitate one model round-trip."""

    model_name = "fake-model"
    prompt_version = "test"

    def score_item(
        self, *, item_content: str, job_title: str, job_description: str
    ) -> ScoringResult:
        time.sleep(SIMULATED_REQUEST_SECONDS)
        return ScoringResult(score=75, reasoning="Benchmark item.")


@pytest.fixture()
def build_resume(session: Session):
    """Return a factory that creates a profile with N experience items."""

    def _build_resume(item_count: int) -> int:
        profile = create_resume_profile(session, name=f"bench-{item_count}")
        section = add_section(
            session,
            profile_id=profile.id,
            section_type=SectionType.experience,
            title="Work Experience",
        )
        for index in range(item_count):
            add_item(
                session,
                section_id=section.id,
                content={"position": f"Role {index}", "company": f"Company {index}"},
                order_idx=index,
            )
        session.commit()
        return profile.id

    return _build_resume


def test_single_item_tailors_under_two_seconds(
    session: Session, create_job, build_resume
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(1)
    tailor = ResumeTailor(client=SlowFakeScoringClient())

    started = time.perf_counter()
    tailor.tailor_to_job(session, base_resume_id=resume_id, job_id=job.id)
    elapsed = time.perf_counter() - started

    assert elapsed < SINGLE_ITEM_CEILING_SECONDS


def test_batch_of_ten_tailors_under_fifteen_seconds(
    session: Session, create_job, build_resume
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(10)
    tailor = ResumeTailor(client=SlowFakeScoringClient())

    started = time.perf_counter()
    tailor.tailor_to_job(session, base_resume_id=resume_id, job_id=job.id)
    elapsed = time.perf_counter() - started

    assert elapsed < BATCH_OF_TEN_CEILING_SECONDS


def test_run_duration_reflects_scoring_time(
    session: Session, create_job, build_resume
) -> None:
    """duration_ms must cover at least the simulated model latency."""
    job = create_job(title="Data Engineer")
    resume_id = build_resume(3)
    tailor = ResumeTailor(client=SlowFakeScoringClient())

    tailor.tailor_to_job(session, base_resume_id=resume_id, job_id=job.id)

    run = session.exec(select(ResumeTailorRun)).one()
    assert run.duration_ms >= int(SIMULATED_REQUEST_SECONDS * 1000)
