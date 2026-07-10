"""Routes for the tracked jobs page and list fragment.

These routes render HTML with Jinja2. HTMX can request smaller HTML fragments,
so the server can update part of the page without writing much JavaScript.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.jobs import list_jobs
from app.services.resume_crud import list_resumes
from app.services.sources import list_sources


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _base_resumes(session: Session):
    """Master resumes only: tailoring a tailored variant compounds filtering."""
    return [resume for resume in list_resumes(session) if resume.base_resume_id is None]


@router.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the complete tracked jobs and job sources page."""
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {
            "jobs": list_jobs(session),
            "sources": list_sources(session),
            "base_resumes": _base_resumes(session),
        },
    )


@router.get(
    "/jobs/partials/list",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def jobs_list_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the jobs list fragment for HTMX retrieval.

    HTMX calls this route from the Refresh button in the template. Returning a
    partial keeps the browser update small and easy to understand.
    """
    jobs = list_jobs(session)
    return templates.TemplateResponse(
        request,
        "_jobs_list.html",
        {"jobs": jobs, "base_resumes": _base_resumes(session)},
    )
