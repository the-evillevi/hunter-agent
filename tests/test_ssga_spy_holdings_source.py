"""Tests for the SSGA/SPY holdings workbook company source."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.config import load_config
from app.services.company_sources import (
    CompanySourceRegistry,
    fetch_company_constituents,
)
from app.services.ssga_spy_holdings import (
    SSGA_SPY_HOLDINGS_URL,
    SSGASpyHoldingsSource,
    SSGAWorkbookParseError,
    skip_reason,
)


FIXTURE_PATH = Path("tests/fixtures/ssga_spy_holdings_representative.xlsx")
FETCHED_AT = datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def test_fetch_parses_representative_holdings_workbook() -> None:
    source = SSGASpyHoldingsSource(
        workbook_path=FIXTURE_PATH,
        now=lambda: FETCHED_AT,
    )

    rows = asyncio.run(source.fetch(load_config()))

    assert [row["Ticker"] for row in rows] == ["NVDA", "BRK.B", "OPT"]
    assert [row["Order"] for row in rows] == [1, 2, 3]
    assert rows[0]["Name"] == "NVIDIA CORP"
    assert rows[0]["Weight"] == 7.972064
    assert rows[0]["Sector"] == "Information Technology"
    assert rows[0]["Shares Held"] == 297490286
    assert rows[0]["Local Currency"] == "USD"
    assert rows[0]["Holdings As Of"] == date(2026, 6, 9)
    assert rows[0]["Source URL"] == SSGA_SPY_HOLDINGS_URL
    assert rows[0]["Fetched At"] == FETCHED_AT
    assert rows[0]["Local Path"].endswith(str(FIXTURE_PATH))


def test_fetch_records_skipped_rows_without_crashing() -> None:
    source = SSGASpyHoldingsSource(
        workbook_path=FIXTURE_PATH,
        now=lambda: FETCHED_AT,
    )

    rows = asyncio.run(source.fetch(load_config()))

    assert len(rows) == 3
    assert [
        (skipped.row_number, skipped.reason) for skipped in source.last_skipped_rows
    ] == [
        (10, "missing required Weight"),
        (11, "non-company holding"),
    ]


def test_contra_corporate_action_position_is_not_a_company() -> None:
    row = {
        "Name": "CONTRA HOLOGIC INCORPO",
        "Ticker": "2602335D",
        "Weight": 0.000003,
        "Sector": "-",
    }

    assert skip_reason(row) == "non-company holding"


def test_normalize_preserves_ticker_punctuation_and_optional_fields() -> None:
    source = SSGASpyHoldingsSource(
        workbook_path=FIXTURE_PATH,
        now=lambda: FETCHED_AT,
    )
    rows = asyncio.run(source.fetch(load_config()))

    company = source.normalize(rows[1], load_config())
    optional_company = source.normalize(rows[2], load_config())

    assert company.symbol == "BRK.B"
    assert company.name == "Berkshire Hathaway Inc. Class B"
    assert company.weight == 1.703
    assert company.order == 2
    assert company.identifier == "084670702"
    assert company.sedol == "2073390"
    assert company.sector == "Financials"
    assert company.shares_held == 12922000
    assert company.local_currency == "USD"
    assert company.raw_metadata["source_url"] == SSGA_SPY_HOLDINGS_URL
    assert company.raw_metadata["holdings_as_of"] == date(2026, 6, 9)
    assert company.raw_metadata["local_path"].endswith(str(FIXTURE_PATH))
    assert optional_company.identifier is None
    assert optional_company.sedol is None
    assert optional_company.sector is None


def test_source_works_through_company_source_protocol() -> None:
    config = load_config()
    registry = CompanySourceRegistry()
    registry.register(
        SSGASpyHoldingsSource(
            workbook_path=FIXTURE_PATH,
            now=lambda: FETCHED_AT,
        )
    )

    companies = asyncio.run(
        fetch_company_constituents(
            registry=registry,
            source_names=["ssga_spy_holdings"],
            config=config,
        )
    )

    assert [(company.symbol, company.name, company.order) for company in companies] == [
        ("NVDA", "NVIDIA CORP", 1),
        ("BRK.B", "Berkshire Hathaway Inc. Class B", 2),
        ("OPT", "Optional Fields Inc", 3),
    ]


def test_missing_holdings_sheet_fails_loudly(tmp_path: Path) -> None:
    workbook_path = tmp_path / "missing-sheet.xlsx"
    workbook = Workbook()
    workbook.active.title = "not_holdings"
    workbook.save(workbook_path)
    source = SSGASpyHoldingsSource(workbook_path=workbook_path)

    with pytest.raises(SSGAWorkbookParseError, match="holdings sheet"):
        asyncio.run(source.fetch(load_config()))


def test_missing_required_header_fails_loudly(tmp_path: Path) -> None:
    workbook_path = tmp_path / "missing-header.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "holdings"
    worksheet.append(["Fund Name:", "State Street® SPDR® S&P 500® ETF Trust"])
    worksheet.append(["Name", "Ticker", "Identifier"])
    worksheet.append(["NVIDIA CORP", "NVDA", "67066G104"])
    workbook.save(workbook_path)
    source = SSGASpyHoldingsSource(workbook_path=workbook_path)

    with pytest.raises(SSGAWorkbookParseError, match="required columns"):
        asyncio.run(source.fetch(load_config()))
