"""Tests for the Remotive job source adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime
import subprocess
import sys
from typing import Any

import httpx
import pytest
from sqlmodel import Session, create_engine

from app.models.source import Source
from app.services.remotive import RemotiveAdapterError, RemotiveJobSourceAdapter
from app.services.sources import JobSourceRunContext, default_source_registry


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Source.__table__.create(engine)
    return Session(engine)


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


def test_remotive_adapter_applies_timeout_to_injected_client() -> None:
    seen_timeout: dict[str, float | None] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_timeout
        seen_timeout = request.extensions.get("timeout")
        return httpx.Response(200, json={"jobs": []}, request=request)

    async def run_fetch() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=None,
        ) as client:
            adapter = RemotiveJobSourceAdapter(client=client, timeout=3.5)
            await adapter.fetch(JobSourceRunContext())

    asyncio.run(run_fetch())

    assert seen_timeout is not None
    assert set(seen_timeout.values()) == {3.5}


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


def test_remotive_adapter_skips_malformed_and_excluded_jobs(caplog) -> None:
    async def run_fetch() -> None:
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

    asyncio.run(run_fetch())

    assert "Skipping Remotive job id=2 title='Recruiter'" in caplog.text
    assert "job matched an excluded keyword" in caplog.text
    assert "Skipping Remotive job id=3 title='AI Engineer'" in caplog.text
    assert "job is missing title, company, or location" in caplog.text
    assert "job payload is not an object" in caplog.text


def test_remotive_adapter_ignores_blank_excluded_keywords() -> None:
    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with make_remotive_client({"jobs": [remotive_job()]}) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            return await adapter.fetch(
                JobSourceRunContext(exclude_keywords=("", "   "))
            )

    jobs = asyncio.run(run_fetch())

    assert len(jobs) == 1


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


def test_remotive_adapter_wraps_transport_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    async def assert_failure() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = RemotiveJobSourceAdapter(client=client)
            with pytest.raises(RemotiveAdapterError, match="request failed"):
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


def test_default_registry_registers_remotive_from_sources_import_only() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.sources import default_source_registry; "
                "print(default_source_registry.resolve_selected(['remotive'])[0]"
                ".adapter.identity.display_name)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "Remotive"


def test_remotive_module_can_be_imported_before_sources() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.services.remotive; "
                "from app.services.sources import default_source_registry; "
                "print(default_source_registry.resolve_selected(['remotive'])[0]"
                ".adapter.identity.display_name)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "Remotive"


def test_scrape_jobs_with_default_registry_can_resolve_remotive() -> None:
    with make_session() as session:
        session.add(Source(name="Remotive", enabled=True))
        session.commit()

        resolved_sources = default_source_registry.resolve_enabled(session)

    assert [
        resolved.adapter.identity.display_name for resolved in resolved_sources
    ] == ["Remotive"]
