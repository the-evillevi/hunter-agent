"""Routes for job source visibility and enablement."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.sources import SourceNotFoundError, list_sources, set_source_enabled


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/sources/partials/list",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def sources_list_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the source enablement list for HTMX retrieval."""
    return templates.TemplateResponse(
        request,
        "_sources_list.html",
        {"sources": list_sources(session)},
    )


@router.post(
    "/sources/{source_id}/toggle",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def toggle_source(
    source_id: int,
    enabled: bool,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Toggle one source and return the refreshed source fragment."""
    try:
        set_source_enabled(session, source_id, enabled)
    except SourceNotFoundError as error:
        raise HTTPException(status_code=404, detail="Source not found") from error

    return templates.TemplateResponse(
        request,
        "_sources_list.html",
        {"sources": list_sources(session)},
    )
