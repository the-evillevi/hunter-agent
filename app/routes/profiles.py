"""HTML routes for database-owned job profile management."""

from collections.abc import Callable
import re
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlmodel import Session, select

from app.db.database import get_session
from app.models.company import Company
from app.models.profile import LocationType, RemotiveCategory
from app.models.source import Source
from app.services.profiles import (
    ProfileConflictError,
    ProfileError,
    ProfileNotFoundError,
    create_profile,
    create_source_query,
    delete_profile,
    delete_source_query,
    list_profiles,
    update_profile,
    update_source_query,
)


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _profiles_context(
    session: Session,
    *,
    error: str | None = None,
) -> dict:
    """Build the template context shared by the full page and the fragment."""
    companies = session.exec(select(Company).order_by(func.lower(Company.name))).all()
    remotive = session.exec(
        select(Source).where(func.lower(Source.name) == "remotive")
    ).first()
    adzuna = session.exec(
        select(Source).where(func.lower(Source.name) == "adzuna")
    ).first()
    try:
        profiles = list_profiles(session)
    except ProfileError as profile_error:
        profiles = []
        error = str(profile_error)
    return {
        "profiles": profiles,
        "companies": companies,
        "adzuna": adzuna,
        "remotive": remotive,
        "remotive_categories": list(RemotiveCategory),
        "location_types": [value.value for value in LocationType],
        "error": error,
    }


def _render_list(
    request: Request,
    session: Session,
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Render the list fragment, optionally with a bounded error banner."""
    return templates.TemplateResponse(
        request,
        "_profiles_list.html",
        _profiles_context(session, error=error),
        status_code=status_code,
    )


@router.get("/profiles", response_class=HTMLResponse)
def profiles_page(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the complete profile-management page."""
    return templates.TemplateResponse(
        request,
        "profiles.html",
        _profiles_context(session),
    )


@router.get(
    "/profiles/partials/list",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def profiles_list_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the replaceable profile cards and source-query forms."""
    return _render_list(request, session)


@router.post("/profiles", response_class=HTMLResponse)
async def create_profile_route(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    form = await _read_form(request)
    try:
        create_profile(session, **_profile_values(form))
    except (ProfileError, ValueError) as error:
        return _profile_error(request, session, error)
    return _render_list(request, session)


@router.patch("/profiles/{profile_id}", response_class=HTMLResponse)
async def update_profile_route(
    profile_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    form = await _read_form(request)
    try:
        update_profile(session, profile_id, **_profile_values(form))
    except (ProfileError, ValueError) as error:
        return _profile_error(request, session, error)
    return _render_list(request, session)


@router.delete("/profiles/{profile_id}", response_class=HTMLResponse)
def delete_profile_route(
    profile_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    try:
        delete_profile(session, profile_id)
    except ProfileError as error:
        return _profile_error(request, session, error)
    return _render_list(request, session)


@router.post(
    "/profiles/{profile_id}/source-queries",
    response_class=HTMLResponse,
)
async def create_source_query_route(
    profile_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    form = await _read_form(request)
    return _mutate_query(
        request,
        session,
        lambda: create_source_query(
            session,
            profile_id=profile_id,
            source_id=_integer(form, "source_id", "source"),
            raw_query=_query_values(form),
        ),
    )


@router.patch(
    "/profiles/{profile_id}/source-queries/{query_id}",
    response_class=HTMLResponse,
)
async def update_source_query_route(
    profile_id: int,
    query_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    form = await _read_form(request)
    return _mutate_query(
        request,
        session,
        lambda: update_source_query(
            session,
            profile_id=profile_id,
            query_id=query_id,
            raw_query=_query_values(form),
        ),
    )


@router.delete(
    "/profiles/{profile_id}/source-queries/{query_id}",
    response_class=HTMLResponse,
)
def delete_source_query_route(
    profile_id: int,
    query_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    try:
        delete_source_query(session, profile_id=profile_id, query_id=query_id)
    except ProfileError as error:
        return _profile_error(request, session, error)
    return _render_list(request, session)


def _mutate_query(
    request: Request,
    session: Session,
    mutation: Callable[[], object],
) -> HTMLResponse:
    """Run one source-query mutation and render the refreshed fragment."""
    try:
        mutation()
    except (ProfileError, ValueError) as error:
        return _profile_error(request, session, error)
    return _render_list(request, session)


def _profile_error(
    request: Request,
    session: Session,
    error: Exception,
) -> HTMLResponse:
    """Map a bounded profile error onto the fragment with a fitting status."""
    if isinstance(error, ProfileNotFoundError):
        status_code = 404
    elif isinstance(error, ProfileConflictError):
        status_code = 409
    else:
        status_code = 400
    return _render_list(
        request,
        session,
        error=str(error),
        status_code=status_code,
    )


async def _read_form(request: Request) -> dict[str, list[str]]:
    return parse_qs((await request.body()).decode(), keep_blank_values=True)


def _profile_values(form: dict[str, list[str]]) -> dict:
    return {
        "role_name": _one(form, "role_name"),
        "salary_min": _integer(form, "salary_min", "salary minimum"),
        "match_threshold": _integer(form, "match_threshold", "match threshold"),
        "active": _one(form, "active", "") == "true",
        "location_types": form.get("location_type", []),
        "keywords": _terms(_one(form, "keywords")),
        "exclude_keywords": _terms(_one(form, "exclude_keywords", "")),
    }


def _query_values(form: dict[str, list[str]]) -> dict:
    raw_query: dict = {"schema_version": 1}
    for field in ("category", "search", "what", "where"):
        value = _one(form, field, "").strip()
        if value:
            raw_query[field] = value
    for field in ("full_time", "permanent"):
        if _one(form, field, "") == "true":
            raw_query[field] = True
    limit = _one(form, "limit", "").strip()
    if limit:
        raw_query["limit"] = _integer(form, "limit", "limit")
    company_id = _one(form, "company_id", "").strip()
    if company_id:
        raw_query["company_id"] = _integer(form, "company_id", "company")
    return raw_query


def _integer(form: dict[str, list[str]], name: str, label: str) -> int:
    raw_value = _one(form, name).strip()
    try:
        return int(raw_value)
    except ValueError as error:
        raise ProfileError(f"{label} must be a whole number") from error


def _one(form: dict[str, list[str]], name: str, default: str | None = None) -> str:
    values = form.get(name)
    if values:
        return values[0]
    if default is not None:
        return default
    raise ValueError(f"{name.replace('_', ' ')} is required")


def _terms(value: str) -> list[str]:
    return re.split(r"[,\n]", value)
