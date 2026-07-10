"""Routes for triggering the job pipeline and reviewing recent runs.

The HTML endpoints serve the dashboard (manual trigger button and the
recent-runs panel); the JSON twin keeps the same entry point reachable for
automation, mirroring the S&P ingestion route pair.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.pipeline import (
    PipelineRunSummary,
    list_recent_pipeline_runs,
    run_job_pipeline,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post(
    "/api/pipeline/run",
    tags=["Pipeline"],
    summary="Run the job pipeline through scoring",
    response_model=PipelineRunSummary,
    responses={
        200: {
            "model": PipelineRunSummary,
            "description": "The run completed (fully or partially).",
        },
        409: {
            "model": PipelineRunSummary,
            "description": "Another run holds the lock; this one was skipped.",
        },
        500: {
            "model": PipelineRunSummary,
            "description": "The run failed before making any progress.",
        },
    },
)
async def run_pipeline_api(
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Run selection, fetch, dedup, and scoring; stop before any CV work."""
    _, summary = await run_job_pipeline(session, trigger_type="manual")
    return JSONResponse(
        summary.model_dump(mode="json"),
        status_code=summary.response_status_code(),
    )


@router.post(
    "/pipeline/run",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def run_pipeline_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Run the pipeline from the dashboard and render a status fragment."""
    _, summary = await run_job_pipeline(session, trigger_type="manual")
    return templates.TemplateResponse(
        request,
        "_pipeline_run_result.html",
        {"summary": summary},
        status_code=summary.response_status_code(),
    )


@router.get(
    "/pipeline/partials/runs",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def pipeline_runs_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the recent-runs panel fragment for HTMX refreshes."""
    return templates.TemplateResponse(
        request,
        "_pipeline_runs.html",
        {"pipeline_runs": list_recent_pipeline_runs(session)},
    )
