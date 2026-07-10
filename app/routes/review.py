"""Routes for the scored-job review queue (HNTR-2).

A full page at /review plus HTMX partials: the paginated table fragment
(filters and sort survive pagination because every generated URL carries
them) and a per-job detail fragment with layer evidence. The explicit
draft action calls the sanctioned service guard from HNTR-52 and
re-renders only the affected row.
"""

from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.services.applications import (
    DuplicateApplicationError,
    create_application_draft,
)
from app.services.blacklist import BlacklistedJobError
from app.services.profiles import list_profiles
from app.services.review_queue import (
    ReviewQueuePage,
    ReviewSort,
    get_review_queue_item,
    get_review_run_detail,
    list_review_queue,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

SortDirection = Literal["1", "0"]


@router.get("/review", response_class=HTMLResponse, include_in_schema=False)
def review_page(
    request: Request,
    profile_id: str | None = None,
    min_score: str | None = None,
    sort: ReviewSort = "score",
    desc: SortDirection = "1",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the complete review queue page with its filter form."""
    return _render_review(
        request,
        session,
        template="review.html",
        profile_id=profile_id,
        min_score=min_score,
        sort=sort,
        desc=desc,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/review/partials/table",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def review_table_partial(
    request: Request,
    profile_id: str | None = None,
    min_score: str | None = None,
    sort: ReviewSort = "score",
    desc: SortDirection = "1",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the review table fragment for HTMX pagination."""
    return _render_review(
        request,
        session,
        template="_review_table.html",
        profile_id=profile_id,
        min_score=min_score,
        sort=sort,
        desc=desc,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/review/partials/jobs/{job_id}/detail",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def review_row_detail_partial(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render one job's layer evidence into its expandable detail row."""
    if get_review_queue_item(session, job_id) is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} was not found")
    detail = get_review_run_detail(session, job_id=job_id)
    return templates.TemplateResponse(
        request,
        "_review_row_detail.html",
        {"job_id": job_id, "detail": detail},
    )


@router.post(
    "/jobs/{job_id}/application",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def start_application_draft(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Explicit user action: promote one reviewed job into a draft."""
    error: str | None = None
    try:
        create_application_draft(session, job_id=job_id)
    except LookupError as lookup_error:
        raise HTTPException(status_code=404, detail=str(lookup_error)) from lookup_error
    except (BlacklistedJobError, DuplicateApplicationError) as guard_error:
        error = str(guard_error)
    return _review_row_response(request, session, job_id, error=error)


def _render_review(
    request: Request,
    session: Session,
    *,
    template: str,
    profile_id: str | None,
    min_score: str | None,
    sort: ReviewSort,
    desc: SortDirection,
    page: int,
    page_size: int,
) -> HTMLResponse:
    """One body for the full page and the table partial.

    Both routes must parse filters identically or the page and its HTMX
    refreshes drift apart; only the template (and the page's profile
    dropdown data) differ.
    """
    try:
        profile_filter = _parse_optional_int("profile_id", profile_id, low=1)
        score_filter = _parse_optional_int("min_score", min_score, low=0, high=100)
    except ValueError as error:
        # An HTML fragment, not JSON: the htmx error hook in base.html
        # only swaps text/html error responses into the page.
        return HTMLResponse(
            f'<div class="alert alert-error text-sm">{error}</div>',
            status_code=400,
        )
    review_page_data = _query_review(
        session,
        profile_id=profile_filter,
        min_score=score_filter,
        sort=sort,
        desc=desc,
        page=page,
        page_size=page_size,
    )
    context = _review_context(
        review_page=review_page_data,
        profile_id=profile_filter,
        min_score=score_filter,
        sort=sort,
        desc=desc,
    )
    if template == "review.html":
        context["profiles"] = list_profiles(session)
    return templates.TemplateResponse(request, template, context)


def _parse_optional_int(
    name: str,
    value: str | None,
    *,
    low: int | None = None,
    high: int | None = None,
) -> int | None:
    """Empty form fields mean "no filter"; garbage is an explicit error.

    FastAPI would reject an empty string for an int query outright, but
    the filter form always submits both fields, so blank must be legal.
    """
    if value is None or value.strip() == "":
        return None
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if (low is not None and number < low) or (high is not None and number > high):
        raise ValueError(f"{name} is out of range")
    return number


def _query_review(
    session: Session,
    *,
    profile_id: int | None,
    min_score: int | None,
    sort: ReviewSort,
    desc: SortDirection,
    page: int,
    page_size: int,
) -> ReviewQueuePage:
    return list_review_queue(
        session,
        profile_id=profile_id,
        min_score=min_score,
        sort=sort,
        descending=desc == "1",
        page=page,
        page_size=page_size,
    )


def _review_context(
    *,
    review_page: ReviewQueuePage,
    profile_id: int | None,
    min_score: int | None,
    sort: ReviewSort,
    desc: SortDirection,
) -> dict:
    def review_url(
        page: int,
        *,
        partial: bool,
        sort_override: ReviewSort | None = None,
        desc_override: SortDirection | None = None,
    ) -> str:
        parameters: dict = {
            "sort": sort_override or sort,
            "desc": desc_override or desc,
            "page": page,
            "page_size": review_page.page_size,
        }
        if profile_id is not None:
            parameters["profile_id"] = profile_id
        if min_score is not None:
            parameters["min_score"] = min_score
        path = "/review/partials/table" if partial else "/review"
        return f"{path}?{urlencode(parameters)}"

    def sort_url(column: ReviewSort, *, partial: bool) -> str:
        # Clicking the active column flips direction; a new column starts
        # descending (best scores / furthest outcomes first).
        flipped: SortDirection = "0" if (sort == column and desc == "1") else "1"
        return review_url(
            1, partial=partial, sort_override=column, desc_override=flipped
        )

    return {
        "review_page": review_page,
        "profile_id": profile_id,
        "min_score": min_score,
        "sort": sort,
        "desc": desc,
        "previous_partial_url": (
            review_url(review_page.previous_page, partial=True)
            if review_page.previous_page is not None
            else None
        ),
        "previous_url": (
            review_url(review_page.previous_page, partial=False)
            if review_page.previous_page is not None
            else None
        ),
        "next_partial_url": (
            review_url(review_page.next_page, partial=True)
            if review_page.next_page is not None
            else None
        ),
        "sort_score_url": sort_url("score", partial=True),
        "sort_score_href": sort_url("score", partial=False),
        "sort_status_url": sort_url("status", partial=True),
        "sort_status_href": sort_url("status", partial=False),
    }


def _review_row_response(
    request: Request,
    session: Session,
    job_id: int,
    *,
    error: str | None = None,
) -> HTMLResponse:
    item = get_review_queue_item(session, job_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} was not found")
    return templates.TemplateResponse(
        request,
        "_review_row.html",
        {"item": item, "review_row_error": error},
        status_code=409 if error else 200,
    )
