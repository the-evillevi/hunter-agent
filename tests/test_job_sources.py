"""Tests for the job source protocol and scraper orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

import httpx
import pytest
from sqlmodel import Session, create_engine

from app.models.source import Source
from app.services.remotive import RemotiveAdapterError, RemotiveJobSourceAdapter
from app.services.scraper import scrape_jobs, scrape_jobs_async
from app.services.sources import (
    JobSourceIdentity,
    JobSourceRegistry,
    JobSourceRunContext,
    NormalizedJob,
    UnknownJobSourceError,
    default_source_registry,
    make_job_identity_hash,
)


class FakeAdapter:
    """Small no-network adapter used to prove the protocol boundary."""

    def __init__(self, source_name: str, raw_jobs: Sequence[Mapping[str, object]]):
        self.identity = JobSourceIdentity(name=source_name)
        self.raw_jobs = list(raw_jobs)
        self.seen_contexts: list[JobSourceRunContext] = []

    async def fetch(
        self, context: JobSourceRunContext
    ) -> Sequence[Mapping[str, object]]:
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


def test_identity_hash_prefers_external_id_over_changing_job_text() -> None:
    first_hash = make_job_identity_hash(
        source_name="remotive",
        external_id="remote-123",
        url="https://jobs.example/ai-engineer",
        title="AI Engineer",
        company="HNTR Labs",
        location="Remote",
    )
    updated_hash = make_job_identity_hash(
        source_name="remotive",
        external_id="remote-123",
        url="https://jobs.example/senior-ai-engineer",
        title="Senior AI Engineer",
        company="HNTR Labs Inc.",
        location="Remote, US",
    )

    assert updated_hash == first_hash


def test_identity_hash_prefers_url_when_external_id_is_missing() -> None:
    first_hash = make_job_identity_hash(
        source_name="adzuna",
        url="https://jobs.example/data-engineer",
        title="Data Engineer",
        company="HNTR Labs",
        location="Remote",
    )
    updated_hash = make_job_identity_hash(
        source_name="adzuna",
        url="https://jobs.example/data-engineer",
        title="Senior Data Engineer",
        company="HNTR Labs Inc.",
        location="Remote, US",
    )

    assert updated_hash == first_hash


def test_identity_hash_falls_back_to_job_text_without_stable_identifiers() -> None:
    first_hash = make_job_identity_hash(
        source_name="manual",
        title="Data Engineer",
        company="HNTR Labs",
        location="Remote",
    )
    updated_hash = make_job_identity_hash(
        source_name="manual",
        title="Senior Data Engineer",
        company="HNTR Labs",
        location="Remote",
    )

    assert updated_hash != first_hash


def test_registry_resolves_adapters_from_enabled_database_sources() -> None:
    registry = JobSourceRegistry()
    remotive = FakeAdapter("remotive", [])
    adzuna = FakeAdapter("adzuna", [])
    registry.register(remotive)
    registry.register(adzuna)

    with make_session() as session:
        session.add(Source(name="Remotive"))
        session.commit()

        resolved_sources = registry.resolve_enabled(session)

    assert len(resolved_sources) == 1
    assert resolved_sources[0].adapter == remotive
    assert resolved_sources[0].db_source is not None
    assert resolved_sources[0].db_source.name == "Remotive"


def test_registry_excludes_disabled_database_sources() -> None:
    registry = JobSourceRegistry()
    remotive = FakeAdapter("remotive", [])
    adzuna = FakeAdapter("adzuna", [])
    registry.register(remotive)
    registry.register(adzuna)

    with make_session() as session:
        session.add(Source(name="Adzuna", enabled=False))
        session.add(Source(name="Remotive", enabled=True))
        session.commit()

        resolved_sources = registry.resolve_enabled(session)

    assert [resolved_source.adapter for resolved_source in resolved_sources] == [
        remotive
    ]
    assert resolved_sources[0].db_source is not None
    assert resolved_sources[0].db_source.name == "Remotive"


def test_registry_can_resolve_explicit_source_selection_without_database() -> None:
    registry = JobSourceRegistry()
    remotive = FakeAdapter("remotive", [])
    registry.register(remotive)

    resolved_sources = registry.resolve_selected(["remotive"])

    assert len(resolved_sources) == 1
    assert resolved_sources[0].adapter == remotive
    assert resolved_sources[0].db_source is None


def test_registry_rejects_unknown_explicit_source_selection() -> None:
    registry = JobSourceRegistry()
    registry.register(FakeAdapter("remotive", []))

    with pytest.raises(UnknownJobSourceError, match="adzuna"):
        registry.resolve_selected(["remotive", "adzuna"])


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


def make_remotive_client(
    payload: object, *, status_code: int = 200
) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def remotive_job(**overrides: object) -> dict[str, object]:
    job = {
        "id": 123,
        "url": "https://remotive.com/remote-jobs/software-dev/ai-engineer-123",
        "title": "AI Engineer",
        "company_name": "HNTR Labs",
        "company_logo": "https://remotive.com/job/123/logo",
        "category": "Software Development",
        "job_type": "full_time",
        "publication_date": "2026-06-23T10:23:26",
        "candidate_required_location": "Worldwide",
        "salary": "$120k",
        "tags": ["python", "agents"],
        "description": "Build useful agents.",
    }
    job.update(overrides)
    return job


def test_remotive_adapter_builds_request_from_source_query() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json={"jobs": [remotive_job()]}, request=request)

    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            return await adapter.fetch(
                JobSourceRunContext(
                    source_query={
                        "category": "software-development",
                        "company_name": "HNTR Labs",
                        "search": "AI engineer",
                        "limit": 10,
                    }
                )
            )

    jobs = asyncio.run(run_fetch())

    assert len(jobs) == 1
    assert seen_request is not None
    assert seen_request.url.params["category"] == "software-development"
    assert seen_request.url.params["company_name"] == "HNTR Labs"
    assert seen_request.url.params["search"] == "AI engineer"
    assert seen_request.url.params["limit"] == "10"


def test_remotive_adapter_normalizes_representative_job() -> None:
    adapter = RemotiveJobSourceAdapter()

    job = adapter.normalize(remotive_job(), JobSourceRunContext(profile_id=42))

    assert job.source_name == "remotive"
    assert job.source_display_name == "Remotive"
    assert job.profile_id == 42
    assert job.title == "AI Engineer"
    assert job.company == "HNTR Labs"
    assert job.location == "Worldwide"
    assert job.external_id == "123"
    assert job.scraped_at != datetime(2026, 6, 23, 10, 23, 26)
    assert job.raw_metadata["publication_date"] == "2026-06-23T10:23:26"
    assert job.raw_metadata["salary"] == "$120k"
    assert job.raw_metadata["job_type"] == "full_time"
    assert job.raw_metadata["tags"] == ["python", "agents"]
    assert job.raw_metadata["payload"]["id"] == 123


def test_remotive_identity_hash_uses_provider_id() -> None:
    adapter = RemotiveJobSourceAdapter()

    first = adapter.normalize(remotive_job(id=123), JobSourceRunContext())
    changed = adapter.normalize(
        remotive_job(
            id=123,
            title="Principal AI Engineer",
            url="https://remotive.com/remote-jobs/software-dev/principal-ai-123",
        ),
        JobSourceRunContext(),
    )

    assert changed.identity_hash == first.identity_hash


def test_remotive_adapter_skips_malformed_and_excluded_jobs() -> None:
    async def run_fetch() -> RemotiveJobSourceAdapter:
        async with make_remotive_client(
            {
                "jobs": [
                    remotive_job(id=1),
                    remotive_job(id=2, title="Recruiter"),
                    remotive_job(id=3, candidate_required_location=""),
                    "not a job",
                ]
            }
        ) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            jobs = await adapter.fetch(
                JobSourceRunContext(exclude_keywords=("recruiter",))
            )
            assert [job["id"] for job in jobs] == [1]
            return adapter

    adapter = asyncio.run(run_fetch())

    assert [skip["reason"] for skip in adapter.skipped_jobs] == [
        "job matched an excluded keyword",
        "job is missing title, company, or location",
        "job payload is not an object",
    ]


def test_remotive_adapter_filters_non_remote_location_types() -> None:
    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with make_remotive_client(
            {
                "jobs": [
                    remotive_job(id=1),
                    remotive_job(id=2, candidate_required_location="Hybrid - US"),
                ]
            }
        ) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            return await adapter.fetch(JobSourceRunContext(location_types=("hybrid",)))

    jobs = asyncio.run(run_fetch())

    assert [job["id"] for job in jobs] == [2]


def test_remotive_adapter_raises_for_http_and_response_failures() -> None:
    async def assert_http_failure() -> None:
        async with make_remotive_client({"error": "nope"}, status_code=503) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            with pytest.raises(RemotiveAdapterError, match="HTTP 503"):
                await adapter.fetch(JobSourceRunContext())

    async def assert_shape_failure() -> None:
        async with make_remotive_client({"jobs": {}}) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            with pytest.raises(RemotiveAdapterError, match="jobs list"):
                await adapter.fetch(JobSourceRunContext())

    asyncio.run(assert_http_failure())
    asyncio.run(assert_shape_failure())


def test_remotive_adapter_raises_for_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    async def assert_failure() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            with pytest.raises(RemotiveAdapterError, match="invalid JSON"):
                await adapter.fetch(JobSourceRunContext())

    asyncio.run(assert_failure())


def test_default_registry_includes_remotive_adapter() -> None:
    with make_session() as session:
        session.add(Source(name="Remotive", enabled=True))
        session.commit()

        resolved_sources = default_source_registry.resolve_enabled(session)

    assert [resolved.adapter.identity.name for resolved in resolved_sources] == [
        "remotive"
    ]


def test_scrape_jobs_with_default_registry_can_resolve_remotive() -> None:
    with make_session() as session:
        session.add(Source(name="Remotive", enabled=True))
        session.commit()

        resolved_sources = default_source_registry.resolve_enabled(session)

    assert [
        resolved.adapter.identity.display_name for resolved in resolved_sources
    ] == ["Remotive"]


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
