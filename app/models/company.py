"""Company database and display models."""

from datetime import date, datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    """Company table from sql/hunter-agent.sql."""

    __tablename__ = "companies"
    __table_args__ = (
        CheckConstraint(
            "sp500_tier IS NULL OR sp500_tier IN "
            "('mag7', 'top100', 'top200', 'top300', 'top400', 'top500')",
            name="ck_companies_sp500_tier",
        ),
        CheckConstraint(
            "sp500_weight_rank IS NULL OR sp500_weight_rank BETWEEN 1 AND 500",
            name="ck_companies_sp500_weight_rank",
        ),
        CheckConstraint("is_sp500 IN (0, 1)", name="ck_companies_is_sp500"),
    )

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
    ticker: str | None = Field(default=None, unique=True)
    exchange: str | None = None
    cik: str | None = None
    sector: str | None = None
    sub_industry: str | None = None
    headquarters: str | None = None
    date_added: date | None = None
    founded: str | None = None
    sp500_source: str | None = None
    sp500_source_url: str | None = None
    is_sp500: bool = Field(default=False, sa_column_kwargs={"server_default": "0"})
    sp500_weight_rank: int | None = None
    sp500_tier: str | None = None
    sp500_provider: str | None = None
    sp500_identifier: str | None = None
    sp500_sedol: str | None = None
    sp500_weight: float | None = None
    sp500_shares_held: float | None = None
    sp500_local_currency: str | None = None
    sp500_holdings_as_of: date | None = None
    sp500_last_seen_at: datetime | None = None
    sp500_last_updated_at: datetime | None = None


class RemovedSp500Company(SQLModel, table=True):
    """Historical record for companies removed from the S&P 500."""

    __tablename__ = "removed_sp500_companies"

    id: int | None = Field(default=None, primary_key=True)
    company_id: int | None = Field(default=None, foreign_key="companies.id")
    ticker: str | None = None
    name: str
    removal_date: date
    removal_reason: str | None = None
    source: str | None = None
    source_url: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
