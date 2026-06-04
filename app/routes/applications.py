from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.applications import list_applications

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
        {"applications": applications},
    )
