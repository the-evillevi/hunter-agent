"""Tests for S&P 500 company metadata persistence."""

from datetime import date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models.company import Company, RemovedSp500Company
from app.models.job import Job


def test_company_model_stores_sp500_metadata(session: Session) -> None:
    """Companies can hold the SPY workbook and enrichment metadata directly."""
    company = Company(
        name="Apple Inc.",
        ticker="AAPL",
        exchange="NASDAQ",
        cik="0000320193",
        sector="Information Technology",
        sub_industry="Technology Hardware, Storage & Peripherals",
        headquarters="Cupertino, California",
        date_added=date(1982, 11, 30),
        founded="1976",
        sp500_source="ssga_spy_holdings",
        sp500_source_url=(
            "https://www.ssga.com/us/en/individual/library-content/products/"
            "fund-data/etfs/us/holdings-daily-us-en-spy.xlsx"
        ),
        is_sp500=True,
        sp500_weight_rank=1,
        sp500_tier="mag7",
        sp500_rank_source="ssga_spy_holdings:weight",
        sp500_rank_status="weight_derived",
        sp500_provider="SSGA",
        sp500_identifier="037833100",
        sp500_sedol="2046251",
        sp500_weight=7.12,
        sp500_shares_held=187_000_000.0,
        sp500_local_currency="USD",
        sp500_holdings_as_of=date(2026, 6, 9),
        sp500_last_seen_at=datetime(2026, 6, 10, 12, 0, 0),
        sp500_last_updated_at=datetime(2026, 6, 10, 12, 5, 0),
    )

    session.add(company)
    session.commit()
    session.refresh(company)

    assert company.id is not None
    assert company.ticker == "AAPL"
    assert company.cik == "0000320193"
    assert company.sp500_tier == "mag7"
    assert company.sp500_rank_source == "ssga_spy_holdings:weight"
    assert company.sp500_rank_status == "weight_derived"
    assert company.sp500_identifier == "037833100"
    assert company.sp500_holdings_as_of == date(2026, 6, 9)


def test_company_ticker_is_globally_unique(session: Session) -> None:
    """Ticker is a single global identity, not exchange- or source-scoped."""
    session.add(Company(name="Alphabet Inc. Class A", ticker="GOOGL"))
    session.commit()

    session.add(Company(name="Alphabet duplicate", ticker="GOOGL"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_company_cik_is_nullable(session: Session) -> None:
    """SSGA Identifier is not guaranteed CIK, so CIK stays optional."""
    company = Company(name="NVIDIA Corporation", ticker="NVDA", cik=None)

    session.add(company)
    session.commit()
    session.refresh(company)

    assert company.cik is None


def test_sp500_tier_is_constrained(session: Session) -> None:
    """Only the agreed S&P 500 tier buckets are accepted."""
    session.add(Company(name="Tesla Inc.", ticker="TSLA", sp500_tier="mega"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_sp500_rank_status_is_constrained(session: Session) -> None:
    """Only the agreed S&P rank provenance statuses are accepted."""
    session.add(Company(name="Ranked Corp", ticker="RANK", sp500_rank_status="guessed"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_is_sp500_has_database_default_and_boolean_constraint(
    session: Session,
) -> None:
    """SQLModel-created schemas match the committed SQL boolean behavior."""
    session.exec(text("INSERT INTO companies (name) VALUES ('Default Corp')"))
    is_sp500 = session.exec(
        text("SELECT is_sp500 FROM companies WHERE name = 'Default Corp'")
    ).one()[0]

    assert is_sp500 == 0

    with pytest.raises(IntegrityError):
        session.exec(
            text("INSERT INTO companies (name, is_sp500) VALUES ('Bad Corp', 2)")
        )


def test_removed_sp500_company_history_preserves_existing_company_references(
    session: Session,
    create_job,
) -> None:
    """Historical removals live separately while jobs keep their company FK."""
    job: Job = create_job(company_name="Acme Index Alumni")
    removed = RemovedSp500Company(
        company_id=job.company_id,
        ticker="ACME",
        name="Acme Index Alumni",
        removal_date=date(2026, 5, 31),
        removal_reason="Removed during quarterly rebalance",
        source="wikipedia",
        source_url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    )

    session.add(removed)
    session.commit()
    session.refresh(removed)

    assert removed.id is not None
    assert removed.company_id == job.company_id
    assert removed.removal_date == date(2026, 5, 31)
    assert session.get(Job, job.id).company_id == job.company_id
