"""HTMX routes for job and company blacklist mutations (HNTR-52).

Job-level mutations re-render only the affected job row; company-level
mutations re-render the whole jobs list because every row of that company
changes state at once. Error responses still return HTML fragments —
base.html forces HTMX to swap them — so duplicates and unknown targets
stay visible in place instead of failing silently.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.blacklist import (
    BlacklistTargetNotFoundError,
    DuplicateBlacklistEntryError,
    add_company_to_blacklist,
    add_job_to_blacklist,
    remove_company_from_blacklist,
    remove_job_from_blacklist,
)
from app.services.jobs import get_job_list_item, list_jobs
from app.services.resume_crud import list_base_resumes


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.post(
    "/jobs/{job_id}/blacklist",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def blacklist_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Block one job and re-render its row."""
    reason = await _form_reason(request)
    try:
        add_job_to_blacklist(session, job_id=job_id, reason=reason)
    except DuplicateBlacklistEntryError as error:
        return _job_row_response(request, session, job_id, error=str(error))
    except BlacklistTargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _job_row_response(request, session, job_id)


@router.delete(
    "/jobs/{job_id}/blacklist",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def unblacklist_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Delete the job's blacklist entry and re-render its row."""
    try:
        remove_job_from_blacklist(session, job_id=job_id)
    except BlacklistTargetNotFoundError as error:
        if get_job_list_item(session, job_id) is None:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return _job_row_response(request, session, job_id, error=str(error))
    return _job_row_response(request, session, job_id)


@router.post(
    "/companies/{company_id}/blacklist",
    response_class=HTMLResponse,
    include_in_schema=False,
)
async def blacklist_company(
    company_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Block a whole company and re-render the jobs list."""
    reason = await _form_reason(request)
    try:
        add_company_to_blacklist(session, company_id=company_id, reason=reason)
    except DuplicateBlacklistEntryError as error:
        return _jobs_list_response(request, session, error=str(error))
    except BlacklistTargetNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _jobs_list_response(request, session)


@router.delete(
    "/companies/{company_id}/blacklist",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def unblacklist_company(
    company_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Delete the company's blacklist entry and re-render the jobs list."""
    try:
        remove_company_from_blacklist(session, company_id=company_id)
    except BlacklistTargetNotFoundError as error:
        return _jobs_list_response(request, session, error=str(error), status=409)
    return _jobs_list_response(request, session)


async def _form_reason(request: Request) -> str | None:
    body = (await request.body()).decode()
    return parse_qs(body, keep_blank_values=True).get("reason", [None])[0]


def _job_row_response(
    request: Request,
    session: Session,
    job_id: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    job = get_job_list_item(session, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} was not found")
    return templates.TemplateResponse(
        request,
        "_job_row.html",
        {
            "job": job,
            "base_resumes": list_base_resumes(session),
            "blacklist_row_error": error,
        },
        status_code=409 if error else 200,
    )


def _jobs_list_response(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status: int = 409,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_jobs_list.html",
        {
            "jobs": list_jobs(session),
            "base_resumes": list_base_resumes(session),
            "blacklist_error": error,
        },
        status_code=status if error else 200,
    )
