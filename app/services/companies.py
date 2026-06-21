"""Read-only company browsing queries for the companies page."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import case, func, or_
from sqlmodel import Session, select

from app.models.company import Company


CompanyMembership = Literal["current", "all"]


@dataclass(frozen=True)
class CompanyPage:
    """One bounded page of companies and its navigation metadata."""

    companies: list[Company]
    page: int
    page_size: int
    total: int
    total_pages: int
    previous_page: int | None
    next_page: int | None

    @property
    def is_out_of_range(self) -> bool:
        return self.total_pages > 0 and self.page > self.total_pages


def list_companies(
    session: Session,
    *,
    q: str | None = None,
    membership: CompanyMembership = "current",
    tier: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> CompanyPage:
    """Return a filtered, deterministically ordered page of companies."""
    filters = []
    normalized_query = q.strip().lower() if q else ""
    if normalized_query:
        filters.append(
            or_(
                func.lower(Company.name).contains(normalized_query, autoescape=True),
                func.lower(func.coalesce(Company.ticker, "")).contains(
                    normalized_query, autoescape=True
                ),
            )
        )
    if membership == "current":
        filters.append(Company.is_sp500.is_(True))
    if tier is not None:
        filters.append(Company.sp500_tier == tier)

    count_statement = select(func.count()).select_from(Company)
    statement = select(Company)
    if filters:
        count_statement = count_statement.where(*filters)
        statement = statement.where(*filters)

    total = session.exec(count_statement).one()
    total_pages = (total + page_size - 1) // page_size
    companies = list(
        session.exec(
            statement.order_by(
                case((Company.sp500_weight_rank.is_(None), 1), else_=0),
                Company.sp500_weight_rank,
                func.lower(Company.name),
                func.lower(func.coalesce(Company.ticker, "")),
                Company.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
    )

    if total_pages and page > total_pages:
        previous_page = total_pages
    elif page > 1:
        previous_page = page - 1
    else:
        previous_page = None

    return CompanyPage(
        companies=companies,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        previous_page=previous_page,
        next_page=page + 1 if page < total_pages else None,
    )
