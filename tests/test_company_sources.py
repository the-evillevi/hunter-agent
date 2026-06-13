"""Tests for the S&P 500 company-source protocol."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence

from app.config import load_config
from app.models.config import AppConfig
from app.services.company_sources import (
    CompanySourceIdentity,
    CompanySourceRegistry,
    NormalizedCompanyConstituent,
    fetch_company_constituents,
)


class FakeCompanySourceAdapter:
    """No-network adapter that proves the company-source boundary."""

    def __init__(self, source_name: str, rows: Sequence[Mapping[str, object]]) -> None:
        self.identity = CompanySourceIdentity(name=source_name)
        self.rows = rows
        self.seen_configs: list[AppConfig] = []

    async def fetch(self, config: AppConfig) -> Sequence[Mapping[str, object]]:
        self.seen_configs.append(config)
        return self.rows

    def normalize(
        self,
        raw_company: Mapping[str, object],
        config: AppConfig,
    ) -> NormalizedCompanyConstituent:
        return NormalizedCompanyConstituent.from_source(
            source=self.identity,
            symbol=str(raw_company["Ticker"]),
            name=str(raw_company["Name"]),
            weight=float(raw_company["Weight"]),
            order=int(raw_company["Order"]),
            identifier=str(raw_company["Identifier"]),
            sedol=str(raw_company["SEDOL"]),
            raw_metadata={"payload": dict(raw_company), "agent": config.agent.name},
        )


def test_normalized_company_constituent_keeps_ssga_core_fields() -> None:
    source = CompanySourceIdentity(name="ssga_spy_holdings", display_name="SSGA SPY")

    company = NormalizedCompanyConstituent.from_source(
        source=source,
        symbol="NVDA",
        name="NVIDIA CORP",
        weight=7.972064,
        order=1,
        identifier="67066G104",
        sedol="2379504",
        raw_metadata={"Local Currency": "USD"},
    )

    assert company.symbol == "NVDA"
    assert company.name == "NVIDIA CORP"
    assert company.weight == 7.972064
    assert company.order == 1
    assert company.source_name == "ssga_spy_holdings"
    assert company.source_display_name == "SSGA SPY"
    assert company.identifier == "67066G104"
    assert company.sedol == "2379504"
    assert company.raw_metadata == {"Local Currency": "USD"}


def test_normalized_company_payload_uses_hntr_33_compatible_names() -> None:
    source = CompanySourceIdentity(name="ssga_spy_holdings")
    company = NormalizedCompanyConstituent.from_source(
        source=source,
        symbol="AAPL",
        name="APPLE INC",
        weight=6.721774,
        order=2,
        identifier="037833100",
        sedol="2046251",
        shares_held=179_731_797.0,
        local_currency="USD",
    )

    assert company.to_company_payload() == {
        "name": "APPLE INC",
        "ticker": "AAPL",
        "sp500_source": "ssga_spy_holdings",
        "sp500_provider": "ssga_spy_holdings",
        "sp500_identifier": "037833100",
        "sp500_sedol": "2046251",
        "sp500_weight": 6.721774,
        "sp500_weight_rank": 2,
        "sp500_shares_held": 179_731_797.0,
        "sp500_local_currency": "USD",
        "is_sp500": True,
        "raw_metadata": {},
    }


def test_company_source_registry_resolves_explicit_source_list() -> None:
    registry = CompanySourceRegistry()
    ssga = FakeCompanySourceAdapter("ssga_spy_holdings", [])
    wikipedia = FakeCompanySourceAdapter("wikipedia_sp500_enrichment", [])
    registry.register(ssga)
    registry.register(wikipedia)

    adapters = registry.resolve_selected(["wikipedia_sp500_enrichment"])

    assert adapters == [wikipedia]


def test_company_source_orchestration_fetches_and_normalizes_with_app_config() -> None:
    config = load_config()
    registry = CompanySourceRegistry()
    adapter = FakeCompanySourceAdapter(
        "ssga_spy_holdings",
        [
            {
                "Order": 1,
                "Name": "NVIDIA CORP",
                "Ticker": "NVDA",
                "Identifier": "67066G104",
                "SEDOL": "2379504",
                "Weight": "7.972064",
            }
        ],
    )
    registry.register(adapter)

    companies = asyncio.run(
        fetch_company_constituents(
            registry=registry,
            source_names=["ssga_spy_holdings"],
            config=config,
        )
    )

    assert adapter.seen_configs == [config]
    assert [
        (company.symbol, company.name, company.weight, company.order)
        for company in companies
    ] == [("NVDA", "NVIDIA CORP", 7.972064, 1)]
    assert companies[0].raw_metadata["agent"] == "hunter-agent"


def test_company_source_orchestration_does_not_persist_results() -> None:
    config = load_config()
    registry = CompanySourceRegistry()
    registry.register(
        FakeCompanySourceAdapter(
            "ssga_spy_holdings",
            [
                {
                    "Order": 2,
                    "Name": "APPLE INC",
                    "Ticker": "AAPL",
                    "Identifier": "037833100",
                    "SEDOL": "2046251",
                    "Weight": "6.721774",
                }
            ],
        )
    )

    companies = asyncio.run(
        fetch_company_constituents(
            registry=registry,
            source_names=["ssga_spy_holdings"],
            config=config,
        )
    )

    assert companies[0].to_company_payload()["ticker"] == "AAPL"
    assert not hasattr(registry, "session")
