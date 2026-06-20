"""Manual S&P 500 company-ingestion routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.sp500_ingestion import (
    Sp500IngestionSummary,
    run_sp500_ingestion,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post(
    "/api/companies/sp500/ingest",
    response_model=Sp500IngestionSummary,
    responses={409: {}, 500: {}, 502: {}},
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


@router.post("/companies/sp500/ingest", response_class=HTMLResponse)
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
