"""Routes for the applications-focused home dashboard."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.applications import APPLICATION_STATUS_ORDER, list_applications
from app.services.dashboard import get_dashboard_metrics
from app.services.pipeline import list_recent_pipeline_runs
from app.services.resume_crud import list_recent_tailor_runs
from app.services.scheduler import next_scheduled_run_time


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
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
            "pipeline_runs": list_recent_pipeline_runs(session),
            "next_run_time": next_scheduled_run_time(
                getattr(request.app.state, "scheduler", None)
            ),
        },
    )
