"""Tests for manual S&P 500 ingestion orchestration and routes."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone

from sqlmodel import Session

from app.config import PROJECT_ROOT, load_config
from app.models.company import Company
from app.models.config import AppConfig
from app.models.source import Source
from app.services.company_sources import (
    CompanySourceIdentity,
    CompanySourceRegistry,
    NormalizedCompanyConstituent,
)
from app.services.sp500_ingestion import (
    Sp500IngestionFailure,
    Sp500IngestionSummary,
    build_company_source_registry,
    resolve_enabled_company_sources,
    run_sp500_ingestion,
)


IMPORTED_AT = datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc)


class FakeCompanySource:
    """Small configurable provider for orchestration tests."""

    def __init__(
        self,
        source_name: str = "ssga_spy_holdings",
        *,
        rows: Sequence[Mapping[str, object]] | None = None,
        fetch_error: Exception | None = None,
        normalize_error: Exception | None = None,
    ) -> None:
        self.identity = CompanySourceIdentity(name=source_name)
        self.rows = rows or []
        self.fetch_error = fetch_error
        self.normalize_error = normalize_error

    async def fetch(self, config: AppConfig) -> Sequence[Mapping[str, object]]:
        del config
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.rows

    def normalize(
        self,
        raw_company: Mapping[str, object],
        config: AppConfig,
    ) -> NormalizedCompanyConstituent:
        del config
        if self.normalize_error is not None:
            raise self.normalize_error
        return NormalizedCompanyConstituent.from_source(
            source=self.identity,
            symbol=str(raw_company["Ticker"]),
            name=str(raw_company["Name"]),
            weight=float(raw_company["Weight"]),
            order=int(raw_company["Order"]),
            raw_metadata={
                "source_url": "https://example.test/spy.xlsx",
                "holdings_as_of": "2026-06-19",
            },
        )


def registry_with(*sources: FakeCompanySource) -> CompanySourceRegistry:
    registry = CompanySourceRegistry()
    for source in sources:
        registry.register(source)
    return registry


def company_row(ticker: str = "NVDA") -> dict[str, object]:
    return {"Ticker": ticker, "Name": f"{ticker} Corp", "Weight": 7.0, "Order": 1}


def test_company_source_enablement_falls_back_to_typed_config(
    session: Session,
) -> None:
    config = load_config()
    registry = registry_with(FakeCompanySource())

    adapters = resolve_enabled_company_sources(
        session,
        config=config,
        registry=registry,
    )

    assert [adapter.identity.name for adapter in adapters] == ["ssga_spy_holdings"]


def test_registry_resolves_relative_workbook_path_from_project_root() -> None:
    config = load_config()
    source_config = config.sources.ssga_spy_holdings.model_copy(
        update={"workbook_path": "fixtures/local-spy.xlsx"}
    )
    config = config.model_copy(
        update={
            "sources": config.sources.model_copy(
                update={"ssga_spy_holdings": source_config}
            )
        }
    )

    registry = build_company_source_registry(config)
    adapter = registry.resolve_selected(["ssga_spy_holdings"])[0]

    assert adapter.workbook_path == PROJECT_ROOT / "fixtures/local-spy.xlsx"


def test_persisted_company_source_toggle_overrides_config(session: Session) -> None:
    session.add(Source(name="ssga_spy_holdings", enabled=False))
    session.commit()

    summary = asyncio.run(
        run_sp500_ingestion(
            session,
            registry=registry_with(FakeCompanySource(rows=[company_row()])),
            now=lambda: IMPORTED_AT,
        )
    )

    assert summary.status == "failed"
    assert summary.response_status_code() == 409
    assert summary.sources == []


def test_manual_ingestion_imports_and_reports_counts(session: Session) -> None:
    summary = asyncio.run(
        run_sp500_ingestion(
            session,
            registry=registry_with(FakeCompanySource(rows=[company_row()])),
            now=lambda: IMPORTED_AT,
        )
    )

    assert summary.status == "success"
    assert summary.model_dump(exclude={"failures"}) == {
        "status": "success",
        "sources": ["ssga_spy_holdings"],
        "created": 1,
        "updated": 0,
        "unchanged": 0,
        "removed_from_index": 0,
        "failed": 0,
    }


def test_provider_failure_does_not_stop_later_enabled_source(session: Session) -> None:
    session.add(Source(name="broken_provider", enabled=True))
    session.commit()
    registry = registry_with(
        FakeCompanySource("broken_provider", fetch_error=RuntimeError("offline")),
        FakeCompanySource(rows=[company_row("AAPL")]),
    )

    summary = asyncio.run(
        run_sp500_ingestion(
            session,
            registry=registry,
            now=lambda: IMPORTED_AT,
        )
    )

    assert summary.status == "partial_failure"
    assert summary.created == 1
    assert summary.failed == 1
    assert summary.failures[0].stage == "provider"
    assert summary.response_status_code() == 502


def test_normalization_failure_does_not_mark_existing_company_removed(
    session: Session,
) -> None:
    company = Company(name="Keep Corp", ticker="KEEP", is_sp500=True)
    session.add(company)
    session.commit()
    source = FakeCompanySource(
        rows=[company_row()],
        normalize_error=ValueError("bad row"),
    )

    summary = asyncio.run(
        run_sp500_ingestion(
            session,
            registry=registry_with(source),
            now=lambda: IMPORTED_AT,
        )
    )

    session.refresh(company)
    assert summary.failed == 1
    assert summary.failures[0].stage == "normalization"
    assert company.is_sp500 is True


def test_failure_status_prefers_persistence_errors() -> None:
    summary = Sp500IngestionSummary(
        failures=[
            Sp500IngestionFailure(
                source="ssga_spy_holdings",
                stage="provider",
                message="offline",
            ),
            Sp500IngestionFailure(
                source="ssga_spy_holdings",
                stage="persistence",
                message="database error",
            ),
        ],
        failed=2,
    )

    assert summary.response_status_code() == 500


def test_json_ingestion_route_returns_structured_non_success(
    client,
    monkeypatch,
) -> None:
    async def fake_run(session: Session) -> Sp500IngestionSummary:
        del session
        return Sp500IngestionSummary(
            status="failed",
            failed=1,
            failures=[
                Sp500IngestionFailure(
                    source="ssga_spy_holdings",
                    stage="provider",
                    message="download failed",
                )
            ],
        )

    monkeypatch.setattr("app.routes.companies.run_sp500_ingestion", fake_run)

    response = client.post("/api/companies/sp500/ingest")

    assert response.status_code == 502
    assert response.json()["status"] == "failed"
    assert response.json()["failures"][0]["message"] == "download failed"


def test_htmx_ingestion_route_renders_error_fragment(client, monkeypatch) -> None:
    async def fake_run(session: Session) -> Sp500IngestionSummary:
        del session
        return Sp500IngestionSummary(
            status="failed",
            failed=1,
            failures=[
                Sp500IngestionFailure(
                    source="company_sources",
                    stage="selection",
                    message="no S&P 500 company sources are enabled",
                )
            ],
        )

    monkeypatch.setattr("app.routes.companies.run_sp500_ingestion", fake_run)

    response = client.post("/companies/sp500/ingest")

    assert response.status_code == 409
    assert "S&amp;P 500 ingestion failed" in response.text
    assert "no S&amp;P 500 company sources are enabled" in response.text
