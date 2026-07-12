"""Performance checks for the fully mocked tailoring pipeline."""

import asyncio
import json
import time
from typing import Any

import pytest
from sqlmodel import Session, select

from app.models.resume import ResumeTailorRun, SectionType
from app.services.ai.completion import CompletionRequest, CompletionResponse
from app.services.resume_crud import add_item, add_section, create_resume_profile
from app.services.resume_scoring import ResumeItemScorer
from app.services.resume_tailor import ResumeTailor
from tailoring_fakes import FakeCompletionProvider, build_tailor


SIMULATED_REQUEST_SECONDS = 0.05
SINGLE_ITEM_CEILING_SECONDS = 2.0
BATCH_OF_TEN_CEILING_SECONDS = 15.0


async def slow_local_response(request: CompletionRequest) -> str:
    await asyncio.sleep(SIMULATED_REQUEST_SECONDS)
    return json.dumps({"score": 75, "reasoning": "Benchmark item."})


def build_slow_tailor() -> ResumeTailor:
    return build_tailor(
        local=FakeCompletionProvider("ollama", "local-test-model", slow_local_response)
    )


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


def run_benchmark(tailor: ResumeTailor, session: Session, resume_id: int, job_id: int):
    return asyncio.run(
        tailor.tailor_to_job(
            session,
            base_resume_id=resume_id,
            job_id=job_id,
        )
    )


def test_single_item_tailors_under_two_seconds(
    session: Session,
    create_job,
    build_resume,
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(1)

    started = time.perf_counter()
    run_benchmark(build_slow_tailor(), session, resume_id, job.id)
    elapsed = time.perf_counter() - started

    assert elapsed < SINGLE_ITEM_CEILING_SECONDS


def test_batch_of_ten_tailors_under_fifteen_seconds(
    session: Session,
    create_job,
    build_resume,
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(10)

    started = time.perf_counter()
    run_benchmark(build_slow_tailor(), session, resume_id, job.id)
    elapsed = time.perf_counter() - started

    assert elapsed < BATCH_OF_TEN_CEILING_SECONDS


def test_run_duration_reflects_scoring_time(
    session: Session,
    create_job,
    build_resume,
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(3)
    run_benchmark(build_slow_tailor(), session, resume_id, job.id)

    run = session.exec(select(ResumeTailorRun)).one()
    assert run.duration_ms >= int(SIMULATED_REQUEST_SECONDS * 1000)
