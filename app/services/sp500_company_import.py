"""Idempotent S&P 500 company upsert flow."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.models.company import Company, RemovedSp500Company
from app.services.sp500_enrichment import EnrichedCompanyConstituent


AUTHORITATIVE_SOURCE_NAME = "ssga_spy_holdings"


@dataclass(frozen=True)
class Sp500ImportFailure:
    """One row that could not be safely imported."""

    symbol: str
    name: str
    reason: str


@dataclass
class Sp500ImportSummary:
    """Counts and row-level failures from one import run."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    removed_from_index: int = 0
    failures: list[Sp500ImportFailure] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return len(self.failures)


def import_sp500_companies(
    session: Session,
    companies: Iterable[EnrichedCompanyConstituent],
    *,
    imported_at: datetime,
    authoritative_source_name: str = AUTHORITATIVE_SOURCE_NAME,
) -> Sp500ImportSummary:
    """Persist enriched S&P constituents without recreating company rows."""
    imported_at = normalize_datetime(imported_at)
    summary = Sp500ImportSummary()
    seen_company_ids: set[int] = set()
    source_companies = list(companies)

    for incoming in source_companies:
        try:
            company = resolve_company(session, incoming)
            if company is None:
                company = Company(name=incoming.name)
                apply_company_payload(
                    company,
                    incoming,
                    imported_at=imported_at,
                    authoritative_source_name=authoritative_source_name,
                )
                session.add(company)
                session.commit()
                session.refresh(company)
                summary.created += 1
            else:
                changed = apply_company_payload(
                    company,
                    incoming,
                    imported_at=imported_at,
                    authoritative_source_name=authoritative_source_name,
                )
                if changed:
                    session.add(company)
                    session.commit()
                    session.refresh(company)
                    summary.updated += 1
                else:
                    summary.unchanged += 1

            if company.id is not None:
                seen_company_ids.add(company.id)
        except (ValueError, SQLAlchemyError) as error:
            session.rollback()
            summary.failures.append(
                Sp500ImportFailure(
                    symbol=incoming.symbol,
                    name=incoming.name,
                    reason=str(error),
                )
            )

    if summary.failed == 0 and should_mark_removals(
        source_companies,
        authoritative_source_name,
    ):
        summary.removed_from_index = mark_removed_companies(
            session,
            seen_company_ids=seen_company_ids,
            source_companies=source_companies,
            imported_at=imported_at,
            authoritative_source_name=authoritative_source_name,
        )

    return summary


def resolve_company(
    session: Session,
    incoming: EnrichedCompanyConstituent,
) -> Company | None:
    """Resolve by strong identity, with name fallback only for blank rows."""
    matches = identity_matches(session, incoming)
    unique_matches = {
        company.id: company for company in matches if company.id is not None
    }
    if len(unique_matches) > 1:
        raise ValueError("conflicting identities matched multiple companies")
    if unique_matches:
        return next(iter(unique_matches.values()))

    name_match = find_company_by_normalized_name(session, incoming.name)
    if name_match is None:
        return None
    if has_strong_identity(name_match):
        raise ValueError("name matched an existing company with conflicting identity")
    return name_match


def identity_matches(
    session: Session,
    incoming: EnrichedCompanyConstituent,
) -> list[Company]:
    """Return existing companies matched by ticker, provider id, SEDOL, or CIK."""
    matches: list[Company] = []
    for field_name, value in identity_values(incoming):
        if value is None:
            continue
        company = session.exec(
            select(Company).where(getattr(Company, field_name) == value)
        ).first()
        if company is not None:
            matches.append(company)
    return matches


def identity_values(
    incoming: EnrichedCompanyConstituent,
) -> list[tuple[str, str | None]]:
    """Return identity values in the agreed conservative precedence order."""
    constituent = incoming.constituent
    raw_metadata = constituent.raw_metadata
    cik = raw_metadata.get("cik") or raw_metadata.get("CIK")
    return [
        ("ticker", normalize_symbol(incoming.symbol)),
        ("sp500_identifier", normalize_optional_text(constituent.identifier)),
        ("sp500_sedol", normalize_optional_text(constituent.sedol)),
        ("cik", normalize_optional_text(cik)),
    ]


def apply_company_payload(
    company: Company,
    incoming: EnrichedCompanyConstituent,
    *,
    imported_at: datetime,
    authoritative_source_name: str,
) -> bool:
    """Update one company and return whether persisted fields changed."""
    before = company_state(company)
    before_without_updated_at = company_state(company, include_updated_at=False)
    constituent = incoming.constituent
    raw_metadata = constituent.raw_metadata
    incoming_is_authoritative = constituent.source_name == authoritative_source_name
    existing_is_authoritative = company.sp500_source == authoritative_source_name

    set_if_present(company, "name", incoming.name)
    set_if_present(company, "ticker", normalize_symbol(incoming.symbol))
    set_if_present(company, "sector", constituent.sector)
    set_if_present(company, "cik", raw_metadata.get("cik") or raw_metadata.get("CIK"))

    company.is_sp500 = True
    company.sp500_last_seen_at = imported_at

    if incoming_is_authoritative or not existing_is_authoritative:
        company.sp500_source = constituent.source_name
        company.sp500_provider = constituent.source_name
        company.sp500_source_url = optional_metadata(raw_metadata, "source_url")
        company.sp500_identifier = constituent.identifier
        company.sp500_sedol = constituent.sedol
        company.sp500_weight = constituent.weight
        company.sp500_weight_rank = incoming.sp500_weight_rank
        company.sp500_tier = incoming.sp500_tier
        company.sp500_rank_source = incoming.sp500_rank_source
        company.sp500_rank_status = incoming.sp500_rank_status
        company.sp500_shares_held = constituent.shares_held
        company.sp500_local_currency = constituent.local_currency
        company.sp500_holdings_as_of = metadata_date(raw_metadata, "holdings_as_of")

    if before_without_updated_at != company_state(company, include_updated_at=False):
        company.sp500_last_updated_at = imported_at

    return before != company_state(company)


def mark_removed_companies(
    session: Session,
    *,
    seen_company_ids: set[int],
    source_companies: list[EnrichedCompanyConstituent],
    imported_at: datetime,
    authoritative_source_name: str,
) -> int:
    """Mark previously active S&P rows missing from the authoritative import."""
    source_url = first_source_url(source_companies)
    removal_date = first_holdings_as_of(source_companies) or imported_at.date()
    active_companies = session.exec(
        select(Company).where(Company.is_sp500 == True)
    ).all()  # noqa: E712
    removed_count = 0
    for company in active_companies:
        if company.id in seen_company_ids:
            continue
        company.is_sp500 = False
        company.sp500_last_updated_at = imported_at
        session.add(company)
        session.add(
            RemovedSp500Company(
                company_id=company.id,
                ticker=company.ticker,
                name=company.name,
                removal_date=removal_date,
                removal_reason="Missing from latest S&P 500 source import",
                source=authoritative_source_name,
                source_url=source_url,
                created_at=imported_at,
            )
        )
        removed_count += 1

    if removed_count:
        session.commit()
    return removed_count


def should_mark_removals(
    companies: list[EnrichedCompanyConstituent],
    authoritative_source_name: str,
) -> bool:
    return any(
        company.constituent.source_name == authoritative_source_name
        for company in companies
    )


def find_company_by_normalized_name(session: Session, name: str) -> Company | None:
    normalized_name = normalize_name(name)
    for company in session.exec(select(Company)).all():
        if normalize_name(company.name) == normalized_name:
            return company
    return None


def has_strong_identity(company: Company) -> bool:
    return any(
        normalize_optional_text(value)
        for value in (
            company.ticker,
            company.sp500_identifier,
            company.sp500_sedol,
            company.cik,
        )
    )


def set_if_present(company: Company, field_name: str, value: Any) -> None:
    normalized = normalize_optional_text(value)
    if normalized is not None:
        setattr(company, field_name, normalized)


def company_state(
    company: Company,
    *,
    include_updated_at: bool = True,
) -> dict[str, Any]:
    state = {
        "name": company.name,
        "ticker": company.ticker,
        "cik": company.cik,
        "sector": company.sector,
        "sp500_source": company.sp500_source,
        "sp500_source_url": company.sp500_source_url,
        "is_sp500": company.is_sp500,
        "sp500_weight_rank": company.sp500_weight_rank,
        "sp500_tier": company.sp500_tier,
        "sp500_rank_source": company.sp500_rank_source,
        "sp500_rank_status": company.sp500_rank_status,
        "sp500_provider": company.sp500_provider,
        "sp500_identifier": company.sp500_identifier,
        "sp500_sedol": company.sp500_sedol,
        "sp500_weight": company.sp500_weight,
        "sp500_shares_held": company.sp500_shares_held,
        "sp500_local_currency": company.sp500_local_currency,
        "sp500_holdings_as_of": company.sp500_holdings_as_of,
        "sp500_last_seen_at": company.sp500_last_seen_at,
    }
    if include_updated_at:
        state["sp500_last_updated_at"] = company.sp500_last_updated_at
    return state


def first_source_url(companies: list[EnrichedCompanyConstituent]) -> str | None:
    for company in companies:
        source_url = optional_metadata(company.constituent.raw_metadata, "source_url")
        if source_url is not None:
            return source_url
    return None


def first_holdings_as_of(companies: list[EnrichedCompanyConstituent]) -> date | None:
    for company in companies:
        holdings_as_of = metadata_date(
            company.constituent.raw_metadata, "holdings_as_of"
        )
        if holdings_as_of is not None:
            return holdings_as_of
    return None


def optional_metadata(metadata: Any, key: str) -> str | None:
    if not isinstance(metadata, dict):
        return None
    return normalize_optional_text(metadata.get(key))


def metadata_date(metadata: Any, key: str) -> date | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get(key)
    return value if isinstance(value, date) else None


def normalize_symbol(value: str) -> str:
    return value.strip().upper()


def normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_name(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def normalize_datetime(value: datetime) -> datetime:
    """Store datetimes in the project's existing SQLite-friendly style."""
    return value.replace(tzinfo=None)
