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
    update_application_status,
)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/applications", response_class=HTMLResponse)
def applications_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the applications list fragment."""
    applications = list_applications(session)
    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "applications": applications,
            "application_statuses": APPLICATION_STATUS_ORDER,
        },
    )


@router.patch("/applications/{application_id}", response_class=HTMLResponse)
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
