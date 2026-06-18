"""Tests for idempotent S&P 500 company imports."""

from __future__ import annotations

from datetime import date, datetime

from sqlmodel import Session, select

from app.models.company import Company, RemovedSp500Company
from app.models.job import Job
from app.services.company_sources import (
    CompanySourceIdentity,
    NormalizedCompanyConstituent,
)
from app.services.sp500_company_import import import_sp500_companies
from app.services.sp500_enrichment import enrich_sp500_rank_and_tier


SSGA = CompanySourceIdentity(name="ssga_spy_holdings", display_name="SSGA SPY")
WIKIPEDIA = CompanySourceIdentity(name="wikipedia_sp500_enrichment")
IMPORTED_AT = datetime(2026, 6, 11, 18, 0)
HOLDINGS_AS_OF = date(2026, 6, 9)
WORKBOOK_URL = "https://example.test/holdings-daily-us-en-spy.xlsx"


def constituent(
    symbol: str,
    *,
    name: str | None = None,
    weight: float,
    order: int,
    identifier: str | None = None,
    sedol: str | None = None,
    sector: str | None = None,
    source: CompanySourceIdentity = SSGA,
) -> NormalizedCompanyConstituent:
    return NormalizedCompanyConstituent.from_source(
        source=source,
        symbol=symbol,
        name=name or f"{symbol} Corp",
        weight=weight,
        order=order,
        identifier=identifier,
        sedol=sedol,
        sector=sector,
        shares_held=1000 + order,
        local_currency="USD",
        raw_metadata={
            "source_url": WORKBOOK_URL,
            "holdings_as_of": HOLDINGS_AS_OF,
        },
    )


def enrich(*companies: NormalizedCompanyConstituent):
    return enrich_sp500_rank_and_tier(list(companies))


def test_import_creates_companies_and_is_idempotent_on_rerun(session: Session) -> None:
    companies = enrich(
        constituent(
            "NVDA",
            name="NVIDIA CORP",
            weight=7.9,
            order=1,
            identifier="67066G104",
            sedol="2379504",
            sector="Information Technology",
        ),
        constituent(
            "AAPL",
            name="APPLE INC",
            weight=6.4,
            order=2,
            identifier="037833100",
            sedol="2046251",
            sector="Information Technology",
        ),
    )

    first = import_sp500_companies(session, companies, imported_at=IMPORTED_AT)
    second = import_sp500_companies(session, companies, imported_at=IMPORTED_AT)

    assert first.created == 2
    assert first.updated == 0
    assert first.unchanged == 0
    assert first.failed == 0
    assert second.created == 0
    assert second.updated == 0
    assert second.unchanged == 2
    assert len(session.exec(select(Company)).all()) == 2

    apple = session.exec(select(Company).where(Company.ticker == "AAPL")).one()
    assert apple.name == "APPLE INC"
    assert apple.is_sp500 is True
    assert apple.sp500_source == "ssga_spy_holdings"
    assert apple.sp500_source_url == WORKBOOK_URL
    assert apple.sp500_weight_rank == 2
    assert apple.sp500_tier == "mag7"
    assert apple.sp500_rank_source == "ssga_spy_holdings:weight"
    assert apple.sp500_rank_status == "weight_derived"
    assert apple.sp500_identifier == "037833100"
    assert apple.sp500_sedol == "2046251"
    assert apple.sp500_weight == 6.4
    assert apple.sp500_shares_held == 1002
    assert apple.sp500_local_currency == "USD"
    assert apple.sp500_holdings_as_of == HOLDINGS_AS_OF
    assert apple.sp500_last_seen_at == IMPORTED_AT
    assert apple.sp500_last_updated_at == IMPORTED_AT


def test_rerun_updates_last_seen_without_changing_last_updated(
    session: Session,
) -> None:
    companies = enrich(
        constituent(
            "NVDA",
            name="NVIDIA CORP",
            weight=7.9,
            order=1,
            identifier="67066G104",
        )
    )
    later_imported_at = datetime(2026, 6, 12, 18, 0)

    first = import_sp500_companies(session, companies, imported_at=IMPORTED_AT)
    second = import_sp500_companies(session, companies, imported_at=later_imported_at)

    company = session.exec(select(Company).where(Company.ticker == "NVDA")).one()
    assert first.created == 1
    assert second.updated == 0
    assert second.unchanged == 1
    assert company.sp500_last_seen_at == later_imported_at
    assert company.sp500_last_updated_at == IMPORTED_AT


def test_import_renames_existing_ticker_company_without_replacing_id(
    session: Session,
    create_job,
) -> None:
    job: Job = create_job(company_name="Old NVIDIA Name")
    company = session.get(Company, job.company_id)
    company.ticker = "NVDA"
    company.sp500_identifier = "67066G104"
    session.add(company)
    session.commit()
    original_id = company.id

    summary = import_sp500_companies(
        session,
        enrich(
            constituent(
                "NVDA",
                name="NVIDIA CORP",
                weight=7.9,
                order=1,
                identifier="67066G104",
            )
        ),
        imported_at=IMPORTED_AT,
    )

    session.refresh(company)
    assert summary.updated == 1
    assert company.id == original_id
    assert company.name == "NVIDIA CORP"
    assert session.get(Job, job.id).company_id == original_id


def test_import_upgrades_name_only_company_and_preserves_job_links(
    session: Session,
    create_job,
) -> None:
    job: Job = create_job(company_name="Acme Holdings")
    original_id = job.company_id

    summary = import_sp500_companies(
        session,
        enrich(
            constituent(
                "ACME",
                name="Acme Holdings",
                weight=0.5,
                order=220,
                identifier="000ACME00",
            )
        ),
        imported_at=IMPORTED_AT,
    )

    company = session.get(Company, original_id)
    assert summary.updated == 1
    assert company.ticker == "ACME"
    assert company.sp500_identifier == "000ACME00"
    assert session.get(Job, job.id).company_id == original_id


def test_import_fails_conflicting_strong_identifier_but_persists_valid_rows(
    session: Session,
) -> None:
    ticker_match = Company(name="Ticker Match", ticker="CON")
    identifier_match = Company(name="Identifier Match", sp500_identifier="ID-CON")
    session.add_all([ticker_match, identifier_match])
    session.commit()

    summary = import_sp500_companies(
        session,
        enrich(
            constituent(
                "CON",
                name="Conflicted Corp",
                weight=1.0,
                order=1,
                identifier="ID-CON",
            ),
            constituent("OKAY", name="Okay Corp", weight=0.8, order=2),
        ),
        imported_at=IMPORTED_AT,
    )

    session.refresh(ticker_match)
    session.refresh(identifier_match)
    okay = session.exec(select(Company).where(Company.ticker == "OKAY")).one()
    assert summary.created == 1
    assert summary.failed == 1
    assert summary.failures[0].symbol == "CON"
    assert "conflicting identities" in summary.failures[0].reason
    assert ticker_match.name == "Ticker Match"
    assert identifier_match.name == "Identifier Match"
    assert okay.name == "Okay Corp"


def test_import_marks_missing_previous_constituents_as_removed(
    session: Session,
) -> None:
    removed_company = Company(
        name="Removed Corp",
        ticker="DROP",
        is_sp500=True,
        sp500_source="ssga_spy_holdings",
        sp500_weight_rank=75,
    )
    session.add(removed_company)
    session.commit()

    summary = import_sp500_companies(
        session,
        enrich(constituent("KEEP", name="Keep Corp", weight=1.0, order=1)),
        imported_at=IMPORTED_AT,
    )

    session.refresh(removed_company)
    removed = session.exec(select(RemovedSp500Company)).one()
    assert summary.created == 1
    assert summary.removed_from_index == 1
    assert removed_company.is_sp500 is False
    assert removed.company_id == removed_company.id
    assert removed.ticker == "DROP"
    assert removed.name == "Removed Corp"
    assert removed.removal_date == HOLDINGS_AS_OF
    assert removed.source == "ssga_spy_holdings"
    assert removed.source_url == WORKBOOK_URL
    assert removed_company.sp500_last_updated_at == IMPORTED_AT


def test_wikipedia_enrichment_does_not_overwrite_ssga_weight_rank_fields(
    session: Session,
) -> None:
    company = Company(
        name="Apple Inc.",
        ticker="AAPL",
        is_sp500=True,
        sp500_source="ssga_spy_holdings",
        sp500_provider="ssga_spy_holdings",
        sp500_weight=6.4,
        sp500_weight_rank=2,
        sp500_tier="mag7",
        sp500_rank_source="ssga_spy_holdings:weight",
        sp500_rank_status="weight_derived",
        sp500_holdings_as_of=HOLDINGS_AS_OF,
    )
    session.add(company)
    session.commit()

    wikipedia_row = NormalizedCompanyConstituent.from_source(
        source=WIKIPEDIA,
        symbol="AAPL",
        name="Apple Inc.",
        weight=None,
        order=1,
        sector="Consumer Electronics",
        raw_metadata={"source_url": "https://en.wikipedia.org/wiki/S%26P_500"},
    )
    summary = import_sp500_companies(
        session,
        enrich_sp500_rank_and_tier([wikipedia_row]),
        imported_at=IMPORTED_AT,
        authoritative_source_name="ssga_spy_holdings",
    )

    session.refresh(company)
    assert summary.updated == 1
    assert company.sector == "Consumer Electronics"
    assert company.sp500_source == "ssga_spy_holdings"
    assert company.sp500_provider == "ssga_spy_holdings"
    assert company.sp500_weight == 6.4
    assert company.sp500_weight_rank == 2
    assert company.sp500_rank_source == "ssga_spy_holdings:weight"
    assert company.sp500_rank_status == "weight_derived"
