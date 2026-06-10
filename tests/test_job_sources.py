"""Tests for the job source protocol and scraper orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from sqlmodel import Session, create_engine

from app.models.source import Source
from app.services.scraper import scrape_jobs, scrape_jobs_async
from app.services.sources import (
    JobSourceIdentity,
    JobSourceRegistry,
    JobSourceRunContext,
    NormalizedJob,
)


class FakeAdapter:
    """Small no-network adapter used to prove the protocol boundary."""

    def __init__(self, source_name: str, raw_jobs: Sequence[Mapping[str, object]]):
        self.identity = JobSourceIdentity(name=source_name)
        self.raw_jobs = list(raw_jobs)
        self.seen_contexts: list[JobSourceRunContext] = []

    async def fetch(self, context: JobSourceRunContext) -> Sequence[Mapping[str, object]]:
        self.seen_contexts.append(context)
        return self.raw_jobs

    def normalize(
        self,
        raw_job: Mapping[str, object],
        context: JobSourceRunContext,
    ) -> NormalizedJob:
        return NormalizedJob.from_source(
            source=self.identity,
            title=str(raw_job["title"]),
            company=str(raw_job["company"]),
            location=str(raw_job["location"]),
            url=str(raw_job["url"]),
            description=str(raw_job["description"]),
            external_id=str(raw_job["id"]),
            raw_metadata={"payload": dict(raw_job), "keywords": context.keywords},
        )


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Source.__table__.create(engine)
    return Session(engine)


def test_normalized_job_keeps_current_job_fields_and_source_metadata() -> None:
    job = NormalizedJob.from_source(
        source=JobSourceIdentity(name="remotive"),
        title="AI Engineer",
        company="HNTR Labs",
        location="Remote",
        url="https://jobs.example/ai-engineer",
        description="Build useful agents.",
        external_id="remote-123",
        raw_metadata={"salary": "$100k"},
    )

    assert job.title == "AI Engineer"
    assert job.company == "HNTR Labs"
    assert job.location == "Remote"
    assert job.url == "https://jobs.example/ai-engineer"
    assert job.source_name == "remotive"
    assert job.external_id == "remote-123"
    assert job.hash
    assert job.identity_hash == job.hash
    assert job.raw_metadata == {"salary": "$100k"}


def test_registry_resolves_adapters_from_enabled_database_sources() -> None:
    registry = JobSourceRegistry()
    remotive = FakeAdapter("remotive", [])
    adzuna = FakeAdapter("adzuna", [])
    registry.register(remotive)
    registry.register(adzuna)

    with make_session() as session:
        session.add(Source(name="remotive"))
        session.commit()

        adapters = registry.resolve_enabled(session)

    assert adapters == [remotive]


def test_registry_can_resolve_explicit_source_selection_without_database() -> None:
    registry = JobSourceRegistry()
    remotive = FakeAdapter("remotive", [])
    registry.register(remotive)

    adapters = registry.resolve_selected(["remotive"])

    assert adapters == [remotive]


def test_scraper_async_orchestration_uses_registered_adapters() -> None:
    registry = JobSourceRegistry()
    registry.register(
        FakeAdapter(
            "remotive",
            [
                {
                    "id": "remote-123",
                    "title": "AI Engineer",
                    "company": "HNTR Labs",
                    "location": "Remote",
                    "url": "https://jobs.example/ai-engineer",
                    "description": "Build useful agents.",
                }
            ],
        )
    )

    jobs = asyncio.run(
        scrape_jobs_async(
            registry=registry,
            source_names=["remotive"],
            context=JobSourceRunContext(keywords=("python", "agents")),
        )
    )

    assert len(jobs) == 1
    assert jobs[0].source_name == "remotive"
    assert jobs[0].raw_metadata["keywords"] == ("python", "agents")


def test_scrape_jobs_sync_wrapper_preserves_compatibility() -> None:
    registry = JobSourceRegistry()
    registry.register(
        FakeAdapter(
            "remotive",
            [
                {
                    "id": "remote-123",
                    "title": "AI Engineer",
                    "company": "HNTR Labs",
                    "location": "Remote",
                    "url": "https://jobs.example/ai-engineer",
                    "description": "Build useful agents.",
                }
            ],
        )
    )

    jobs = scrape_jobs(
        registry=registry,
        source_names=["remotive"],
        context=JobSourceRunContext(keywords=("python",)),
    )

    assert jobs == [
        {
            "profile_id": None,
            "title": "AI Engineer",
            "company": "HNTR Labs",
            "company_id": None,
            "location": "Remote",
            "location_id": None,
            "url": "https://jobs.example/ai-engineer",
            "source": "remotive",
            "source_id": None,
            "source_name": "remotive",
            "source_display_name": "remotive",
            "description": "Build useful agents.",
            "hash": jobs[0]["hash"],
            "identity_hash": jobs[0]["identity_hash"],
            "external_id": "remote-123",
            "scraped_at": jobs[0]["scraped_at"],
            "score": None,
            "score_reasoning": None,
            "raw_metadata": {
                "payload": {
                    "id": "remote-123",
                    "title": "AI Engineer",
                    "company": "HNTR Labs",
                    "location": "Remote",
                    "url": "https://jobs.example/ai-engineer",
                    "description": "Build useful agents.",
                },
                "keywords": ("python",),
            },
        }
    ]
