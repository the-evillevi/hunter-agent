"""Company browsing and manual S&P 500 ingestion routes."""

from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.companies import CompanyPage, list_companies
from app.services.sp500_ingestion import (
    Sp500IngestionSummary,
    run_sp500_ingestion,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

CompanyTier = Literal["all", "mag7", "top100", "top200", "top300", "top400", "top500"]
CompanyMembership = Literal["current", "all"]


def _companies_context(
    *,
    company_page: CompanyPage,
    q: str | None,
    membership: CompanyMembership,
    tier: CompanyTier,
) -> dict:
    """Build shared template state, including filter-preserving page URLs."""
    query = q.strip() if q else ""

    def page_url(page: int, *, partial: bool) -> str:
        parameters = {
            "membership": membership,
            "tier": tier,
            "page": page,
            "page_size": company_page.page_size,
        }
        if query:
            parameters["q"] = query
        path = "/companies/partials/table" if partial else "/companies"
        return f"{path}?{urlencode(parameters)}"

    return {
        "company_page": company_page,
        "q": query,
        "membership": membership,
        "tier": tier,
        "previous_url": (
            page_url(company_page.previous_page, partial=False)
            if company_page.previous_page is not None
            else None
        ),
        "previous_partial_url": (
            page_url(company_page.previous_page, partial=True)
            if company_page.previous_page is not None
            else None
        ),
        "next_url": (
            page_url(company_page.next_page, partial=False)
            if company_page.next_page is not None
            else None
        ),
        "next_partial_url": (
            page_url(company_page.next_page, partial=True)
            if company_page.next_page is not None
            else None
        ),
    }


def _query_companies(
    session: Session,
    *,
    q: str | None,
    membership: CompanyMembership,
    tier: CompanyTier,
    page: int,
    page_size: int,
) -> CompanyPage:
    return list_companies(
        session,
        q=q,
        membership=membership,
        tier=None if tier == "all" else tier,
        page=page,
        page_size=page_size,
    )


@router.get("/companies", response_class=HTMLResponse, include_in_schema=False)
def companies_page(
    request: Request,
    q: str | None = None,
    membership: CompanyMembership = "current",
    tier: CompanyTier = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the complete, filterable companies page."""
    company_page = _query_companies(
        session,
        q=q,
        membership=membership,
        tier=tier,
        page=page,
        page_size=page_size,
    )
    return templates.TemplateResponse(
        request,
        "companies.html",
        _companies_context(
            company_page=company_page,
            q=q,
            membership=membership,
            tier=tier,
        ),
    )


@router.get(
    "/companies/partials/table",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def companies_table_partial(
    request: Request,
    q: str | None = None,
    membership: CompanyMembership = "current",
    tier: CompanyTier = "all",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the replaceable company table and pagination fragment."""
    company_page = _query_companies(
        session,
        q=q,
        membership=membership,
        tier=tier,
        page=page,
        page_size=page_size,
    )
    return templates.TemplateResponse(
        request,
        "_companies_table.html",
        _companies_context(
            company_page=company_page,
            q=q,
            membership=membership,
            tier=tier,
        ),
    )


@router.post(
    "/api/companies/sp500/ingest",
    tags=["Ingestion"],
    summary="Run the S&P 500 company ingestion",
    response_model=Sp500IngestionSummary,
    responses={
        409: {
            "model": Sp500IngestionSummary,
            "description": "No enabled S&P company source to run.",
        },
        500: {
            "model": Sp500IngestionSummary,
            "description": "Every selected source failed before persistence.",
        },
        502: {
            "model": Sp500IngestionSummary,
            "description": "An upstream provider returned unusable data.",
        },
    },
)
async def ingest_sp500_api(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Run enabled S&P sources and return an automation-friendly summary."""
    summary = await run_sp500_ingestion(session)
    return JSONResponse(
        status_code=summary.response_status_code(),
        content=summary.model_dump(mode="json"),
    )


@router.post(
    "/companies/sp500/ingest",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def ingest_sp500_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Run enabled S&P sources and render an HTMX status fragment."""
    summary = await run_sp500_ingestion(session)
    return templates.TemplateResponse(
        request,
        "_sp500_ingestion_result.html",
        {"summary": summary},
        status_code=summary.response_status_code(),
    )
