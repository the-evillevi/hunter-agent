"""Routes for the main jobs UI.

These routes render HTML with Jinja2. HTMX can request smaller HTML fragments,
so the server can update part of the page without writing much JavaScript.
"""

from sqlite3 import Connection

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.database import get_connection
from app.services.jobs import list_jobs


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def home(request: Request, connection: Connection = Depends(get_connection)) -> HTMLResponse:
    """Render the home page.

    The home page includes a jobs section. Later, HTMX can refresh that section
    after a scrape without reloading the whole browser page.
    """
    jobs = list_jobs(connection)
    return templates.TemplateResponse(
        request,
        "index.html",
        {"jobs": jobs},
    )


@router.get("/jobs", response_class=HTMLResponse)
def jobs_partial(
    request: Request,
    connection: Connection = Depends(get_connection),
) -> HTMLResponse:
    """Render only the jobs list fragment.

    HTMX calls this route from the Refresh button in the template. Returning a
    partial keeps the browser update small and easy to understand.
    """
    jobs = list_jobs(connection)
    return templates.TemplateResponse(
        request,
        "jobs.html",
        {"jobs": jobs},
    )
