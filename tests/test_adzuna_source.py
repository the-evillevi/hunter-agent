"""Tests for the Adzuna job source adapter."""

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

from app.models.config import AdzunaSourceConfig
from app.models.source import Source
from app.services.adzuna import AdzunaAdapterError, AdzunaJobSourceAdapter
from app.services.sources import JobSourceRunContext, default_source_registry


def make_session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Source.__table__.create(engine)
    return Session(engine)


def adzuna_settings(**overrides: object) -> AdzunaSourceConfig:
    values = {
        "enabled": True,
        "app_id": "real-app-id",
        "app_key": "real-app-key",
        "country": "us",
        "results_per_page": 2,
        "max_pages": 3,
    }
    values.update(overrides)
    return AdzunaSourceConfig.model_validate(values)


def make_adzuna_client(payload: object, *, status_code: int = 200) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def adzuna_job(**overrides: object) -> dict[str, object]:
    job = {
        "id": "123",
        "redirect_url": "https://www.adzuna.com/details/123",
        "title": "AI Engineer",
        "company": {"display_name": "HNTR Labs"},
        "location": {
            "display_name": "Remote, US",
            "area": ["US", "Remote"],
        },
        "description": "Build useful agents.",
        "created": "2026-06-23T10:23:26Z",
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_is_predicted": 0,
        "category": {"label": "IT Jobs", "tag": "it-jobs"},
        "contract_time": "full_time",
        "contract_type": "permanent",
        "latitude": 40.7128,
        "longitude": -74.006,
    }
    job.update(overrides)
    return job


def test_adzuna_adapter_builds_authenticated_request_from_context() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json={"results": []}, request=request)

    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(country="gb", results_per_page=25),
            )
            return await adapter.fetch(
                JobSourceRunContext(
                    exclude_keywords=("security clearance",),
                    salary_min=60_000,
                    source_query={
                        "what": "AI engineer",
                        "where": "London",
                        "category": "it-jobs",
                        "full_time": True,
                        "permanent": True,
                    },
                )
            )

    jobs = asyncio.run(run_fetch())

    assert jobs == []
    assert seen_request is not None
    assert seen_request.url.path == "/v1/api/jobs/gb/search/1"
    assert seen_request.url.params["app_id"] == "real-app-id"
    assert seen_request.url.params["app_key"] == "real-app-key"
    assert seen_request.url.params["results_per_page"] == "25"
    assert seen_request.url.params["what"] == "AI engineer"
    assert seen_request.url.params["where"] == "London"
    assert seen_request.url.params["category"] == "it-jobs"
    assert seen_request.url.params["what_exclude"] == "security clearance"
    assert seen_request.url.params["salary_min"] == "60000"
    assert seen_request.url.params["full_time"] == "1"
    assert seen_request.url.params["permanent"] == "1"


def test_adzuna_adapter_applies_timeout_to_injected_client() -> None:
    seen_timeout: dict[str, float | None] | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_timeout
        seen_timeout = request.extensions.get("timeout")
        return httpx.Response(200, json={"results": []}, request=request)

    async def run_fetch() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            timeout=None,
        ) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(),
                timeout=4.5,
            )
            await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    asyncio.run(run_fetch())

    assert seen_timeout is not None
    assert set(seen_timeout.values()) == {4.5}


def test_adzuna_adapter_paginates_and_stops_on_short_page() -> None:
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_pages.append(request.url.path.rsplit("/", 1)[-1])
        if seen_pages[-1] == "1":
            payload = {"results": [adzuna_job(id="1"), adzuna_job(id="2")]}
        else:
            payload = {"results": [adzuna_job(id="3")]}
        return httpx.Response(200, json=payload, request=request)

    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(results_per_page=2, max_pages=5),
            )
            return await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    jobs = asyncio.run(run_fetch())

    assert [job["id"] for job in jobs] == ["1", "2", "3"]
    assert seen_pages == ["1", "2"]


def test_adzuna_adapter_stops_at_configured_max_pages() -> None:
    seen_pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_pages.append(request.url.path.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            json={"results": [adzuna_job(id=f"{seen_pages[-1]}-1")]},
            request=request,
        )

    async def run_fetch() -> Sequence[Mapping[str, Any]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(results_per_page=1, max_pages=2),
            )
            return await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    jobs = asyncio.run(run_fetch())

    assert [job["id"] for job in jobs] == ["1-1", "2-1"]
    assert seen_pages == ["1", "2"]


def test_adzuna_adapter_fails_run_when_later_page_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.path.rsplit("/", 1)[-1]
        if page == "1":
            return httpx.Response(
                200,
                json={"results": [adzuna_job(id="1"), adzuna_job(id="2")]},
                request=request,
            )
        return httpx.Response(429, json={"error": "rate limit"}, request=request)

    async def assert_failure() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(results_per_page=2, max_pages=2),
            )
            with pytest.raises(AdzunaAdapterError, match="HTTP 429"):
                await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    asyncio.run(assert_failure())


def test_adzuna_adapter_normalizes_representative_job() -> None:
    adapter = AdzunaJobSourceAdapter(settings=adzuna_settings())

    job = adapter.normalize(adzuna_job(), JobSourceRunContext(profile_id=42))

    assert job.source_name == "adzuna"
    assert job.source_display_name == "Adzuna"
    assert job.profile_id == 42
    assert job.title == "AI Engineer"
    assert job.company == "HNTR Labs"
    assert job.location == "Remote, US"
    assert job.external_id == "123"
    assert job.url == "https://www.adzuna.com/details/123"
    assert job.scraped_at != datetime(2026, 6, 23, 10, 23, 26)
    assert job.raw_metadata["created"] == "2026-06-23T10:23:26Z"
    assert job.raw_metadata["salary_min"] == 120000
    assert job.raw_metadata["category"]["tag"] == "it-jobs"
    assert job.raw_metadata["location_area"] == ["US", "Remote"]
    assert job.raw_metadata["payload"]["id"] == "123"


def test_adzuna_identity_hash_uses_provider_id() -> None:
    adapter = AdzunaJobSourceAdapter(settings=adzuna_settings())

    first = adapter.normalize(adzuna_job(id="123"), JobSourceRunContext())
    changed = adapter.normalize(
        adzuna_job(
            id="123",
            title="Principal AI Engineer",
            redirect_url="https://www.adzuna.com/details/changed",
        ),
        JobSourceRunContext(),
    )

    assert changed.identity_hash == first.identity_hash


def test_adzuna_adapter_skips_malformed_records(caplog) -> None:
    async def run_fetch() -> None:
        async with make_adzuna_client(
            {
                "results": [
                    adzuna_job(id="1"),
                    adzuna_job(id="2", company={}),
                    adzuna_job(id="", redirect_url=""),
                    "not a job",
                ]
            }
        ) as client:
            adapter = AdzunaJobSourceAdapter(
                client=client,
                settings=adzuna_settings(results_per_page=10),
            )
            jobs = await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))
            assert [job["id"] for job in jobs] == ["1"]

    asyncio.run(run_fetch())

    assert "Skipping Adzuna job id=2 title='AI Engineer'" in caplog.text
    assert "job is missing title, company, location, or stable link" in caplog.text
    assert "job payload is not an object" in caplog.text


def test_adzuna_adapter_raises_for_http_json_and_shape_failures() -> None:
    async def assert_http_failure() -> None:
        async with make_adzuna_client({"error": "nope"}, status_code=503) as client:
            adapter = AdzunaJobSourceAdapter(client=client, settings=adzuna_settings())
            with pytest.raises(AdzunaAdapterError, match="HTTP 503"):
                await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    async def assert_shape_failure() -> None:
        async with make_adzuna_client({"results": {}}) as client:
            adapter = AdzunaJobSourceAdapter(client=client, settings=adzuna_settings())
            with pytest.raises(AdzunaAdapterError, match="results list"):
                await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    asyncio.run(assert_http_failure())
    asyncio.run(assert_shape_failure())


def test_adzuna_adapter_raises_for_invalid_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json", request=request)

    async def assert_failure() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(client=client, settings=adzuna_settings())
            with pytest.raises(AdzunaAdapterError, match="invalid JSON"):
                await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    asyncio.run(assert_failure())


def test_adzuna_adapter_wraps_transport_failures_without_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network unavailable", request=request)

    async def assert_failure() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            adapter = AdzunaJobSourceAdapter(client=client, settings=adzuna_settings())
            with pytest.raises(AdzunaAdapterError) as error:
                await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))
            assert "real-app-key" not in str(error.value)

    asyncio.run(assert_failure())


def test_adzuna_adapter_rejects_placeholder_credentials() -> None:
    async def assert_failure() -> None:
        adapter = AdzunaJobSourceAdapter(
            settings=adzuna_settings(
                enabled=False,
                app_id="YOUR_ADZUNA_APP_ID",
                app_key="YOUR_ADZUNA_APP_KEY",
            )
        )
        with pytest.raises(AdzunaAdapterError, match="credentials"):
            await adapter.fetch(JobSourceRunContext(source_query={"what": "AI"}))

    asyncio.run(assert_failure())


def test_adzuna_adapter_loads_config_lazily(monkeypatch) -> None:
    def fail_if_loaded():
        raise AssertionError("config should not be loaded during construction")

    monkeypatch.setattr("app.services.adzuna.load_config", fail_if_loaded)

    adapter = AdzunaJobSourceAdapter()

    assert adapter.identity.name == "adzuna"


def test_default_registry_includes_adzuna_adapter() -> None:
    with make_session() as session:
        session.add(Source(name="Adzuna", enabled=True))
        session.commit()

        resolved_sources = default_source_registry.resolve_enabled(session)

    assert [resolved.adapter.identity.name for resolved in resolved_sources] == [
        "adzuna"
    ]


def test_default_registry_registers_adzuna_from_sources_import_only() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from app.services.sources import default_source_registry; "
                "print(default_source_registry.resolve_selected(['adzuna'])[0]"
                ".adapter.identity.display_name)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "Adzuna"


def test_adzuna_module_can_be_imported_before_sources() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import app.services.adzuna; "
                "from app.services.sources import default_source_registry; "
                "print(default_source_registry.resolve_selected(['adzuna'])[0]"
                ".adapter.identity.display_name)"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "Adzuna"
