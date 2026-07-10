"""Routes for the applications-focused home dashboard."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.applications import APPLICATION_STATUS_ORDER, list_applications
from app.services.dashboard import get_dashboard_metrics
from app.services.resume_crud import list_recent_tailor_runs


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    """Render application metrics and the tracked application list."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "dashboard_metrics": get_dashboard_metrics(session),
            "applications": list_applications(session),
            "application_statuses": APPLICATION_STATUS_ORDER,
            "recent_tailor_runs": list_recent_tailor_runs(session),
        },
    )
