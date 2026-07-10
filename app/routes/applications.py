from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.models.application import ApplicationStatus
from app.services.applications import (
    APPLICATION_STATUS_ORDER,
    list_applications,
    update_application_notes,
    update_application_status,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get(
    "/applications/partials/list",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def applications_list_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the applications list fragment for HTMX retrieval."""
    applications = list_applications(session)
    return templates.TemplateResponse(
        request,
        "_applications_list.html",
        {
            "applications": applications,
            "application_statuses": APPLICATION_STATUS_ORDER,
        },
    )


@router.patch(
    "/applications/{application_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def update_application_status_card(
    application_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Update one application status and render its card fragment."""
    body = (await request.body()).decode()
    form = parse_qs(body)
    raw_status = form.get("status", [None])[0]

    try:
        status = ApplicationStatus(raw_status)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400, detail="Invalid application status"
        ) from error

    try:
        application = update_application_status(
            session,
            application_id=application_id,
            status=status,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    return templates.TemplateResponse(
        request,
        "_application_card.html",
        {
            "application": application,
            "application_statuses": APPLICATION_STATUS_ORDER,
        },
    )


@router.patch(
    "/applications/{application_id}/notes",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def update_application_notes_card(
    application_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Save (or clear) one application's notes and render its card.

    ``keep_blank_values`` matters: submitting an emptied textarea must
    reach the service as an empty string so it clears the stored notes.
    """
    body = (await request.body()).decode()
    form = parse_qs(body, keep_blank_values=True)
    notes = form.get("notes", [""])[0]

    try:
        application = update_application_notes(
            session,
            application_id=application_id,
            notes=notes,
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    return templates.TemplateResponse(
        request,
        "_application_card.html",
        {
            "application": application,
            "application_statuses": APPLICATION_STATUS_ORDER,
        },
    )
