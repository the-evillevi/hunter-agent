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


SIMULATED_REQUEST_SECONDS = 0.05
SINGLE_ITEM_CEILING_SECONDS = 2.0
BATCH_OF_TEN_CEILING_SECONDS = 15.0


class FakeProvider:
    def __init__(self, provider_name: str, model: str, responder) -> None:
        self.provider_name = provider_name
        self.model = model
        self.responder = responder

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        text = await self.responder(request)
        return CompletionResponse(
            text=text,
            provider=self.provider_name,
            model=self.model,
            duration_ms=1,
            finish_reason="stop",
        )


async def slow_local_response(request: CompletionRequest) -> str:
    await asyncio.sleep(SIMULATED_REQUEST_SECONDS)
    return json.dumps({"score": 75, "reasoning": "Benchmark item."})


def _source_document(prompt: str) -> dict[str, Any]:
    for marker in ("TRUSTED_RESUME_JSON:", "TRUSTED_SOURCE_JSON:"):
        if marker in prompt:
            document, _end = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
            return document
    raise AssertionError("trusted source JSON marker missing")


async def generator_response(request: CompletionRequest) -> str:
    source = _source_document(request.prompt)
    return json.dumps(
        {
            "sections": [
                {
                    "section_type": section["section_type"],
                    "title": section["title"],
                    "items": [
                        {
                            "source_item_id": item["source_item_id"],
                            "content_json": json.dumps(item["content"]),
                        }
                        for item in section["items"]
                        if item["eligible_for_tailoring"]
                    ],
                }
                for section in source["sections"]
                if any(item["eligible_for_tailoring"] for item in section["items"])
            ]
        }
    )


async def critic_response(request: CompletionRequest) -> str:
    return json.dumps(
        {
            "fit_summary": "Benchmark fit.",
            "missing_evidence": [],
            "overclaims": [],
            "required_changes": [],
        }
    )


def build_tailor() -> ResumeTailor:
    return ResumeTailor(
        scorer=ResumeItemScorer(
            FakeProvider("ollama", "local-test-model", slow_local_response)
        ),
        generator=FakeProvider("openai", "gpt-5.5", generator_response),
        critic=FakeProvider("openai", "gpt-5.5", critic_response),
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
    run_benchmark(build_tailor(), session, resume_id, job.id)
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
    run_benchmark(build_tailor(), session, resume_id, job.id)
    elapsed = time.perf_counter() - started

    assert elapsed < BATCH_OF_TEN_CEILING_SECONDS


def test_run_duration_reflects_scoring_time(
    session: Session,
    create_job,
    build_resume,
) -> None:
    job = create_job(title="Data Engineer")
    resume_id = build_resume(3)
    run_benchmark(build_tailor(), session, resume_id, job.id)

    run = session.exec(select(ResumeTailorRun)).one()
    assert run.duration_ms >= int(SIMULATED_REQUEST_SECONDS * 1000)
