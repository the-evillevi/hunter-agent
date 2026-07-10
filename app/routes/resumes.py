"""Resume management routes: browse, create, edit, tailor, and export.

Full pages live at /resumes and /resumes/{id}; HTMX fragments live under
/resumes/partials/ and are excluded from the OpenAPI schema. Tailoring is a
mutation with a meaningful URL that returns an HTML fragment for the modal
target, following the existing applications routes. The job-scoped tailor
route (POST /jobs/{job_id}/tailor) also lives here so both entry points
share one ResumeTailor wiring and result fragment.
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlmodel import Session

from app.db.database import get_session
from app.models.resume import SectionType
from app.services.jobs import list_jobs
from app.services.resume_compiler import ResumeCompiler, ResumeExportError
from app.services.resume_crud import (
    create_resume_profile,
    get_resume_detail,
    list_recent_tailor_runs,
    list_resumes,
    update_item_content,
    update_resume_profile,
    update_section_order,
)
from app.services.resume_import import ResumeDocument, import_resume
from app.services.resume_tailor import ResumeTailor

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

EXPORT_FORMATS = ("json", "json_resume", "html", "pdf")


@router.get("/resumes", response_class=HTMLResponse)
def resumes_page(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the resumes page with base profiles and tailored variants."""
    resumes = list_resumes(session)
    return templates.TemplateResponse(request, "resumes.html", {"resumes": resumes})


@router.get(
    "/resumes/partials/list",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def resumes_list_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render only the resumes list fragment for HTMX retrieval."""
    resumes = list_resumes(session)
    return templates.TemplateResponse(
        request, "_resumes_list.html", {"resumes": resumes}
    )


@router.get(
    "/resumes/partials/recent",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def recent_tailor_runs_partial(
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render the dashboard's recently-tailored-resumes fragment."""
    return templates.TemplateResponse(
        request,
        "_recent_tailor_runs.html",
        {"recent_tailor_runs": list_recent_tailor_runs(session)},
    )


@router.post("/resumes", status_code=201)
async def create_resume(
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Create a resume profile from structured JSON.

    Accepts either a full document (the cvs/resume.json shape validated by
    ResumeDocument) or a minimal ``{"name": ...}`` for an empty profile to
    fill in later. Binary upload parsing (DOCX/PDF) is out of scope.
    """
    try:
        payload = json.loads((await request.body()).decode() or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Body must be JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")

    if "sections" in payload or "basics" in payload:
        try:
            document = ResumeDocument.model_validate(payload)
        except ValidationError as error:
            raise HTTPException(status_code=422, detail=error.errors()) from error
        profile = import_resume(session, document)
    else:
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(
                status_code=422, detail="A non-empty 'name' is required"
            )
        profile = create_resume_profile(session, name=name.strip())
        session.commit()
        session.refresh(profile)

    return JSONResponse(
        {"id": profile.id, "name": profile.name},
        status_code=201,
        headers={"Location": f"/resumes/{profile.id}"},
    )


@router.patch("/resumes/{resume_id}")
async def update_resume(
    resume_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> JSONResponse:
    """Apply partial edits: profile name, item content/order, section order."""
    try:
        payload = json.loads((await request.body()).decode() or "null")
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=422, detail="Body must be JSON") from error
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")

    if get_resume_detail(session, resume_id) is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id} not found")

    try:
        if "name" in payload:
            update_resume_profile(session, profile_id=resume_id, name=payload["name"])
        for item_edit in payload.get("items", []):
            update_item_content(
                session,
                profile_id=resume_id,
                item_id=item_edit["id"],
                content=item_edit.get("content"),
                order_idx=item_edit.get("order_idx"),
            )
        for section_edit in payload.get("sections", []):
            update_section_order(
                session,
                profile_id=resume_id,
                section_id=section_edit["id"],
                order_idx=section_edit["order_idx"],
            )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (KeyError, TypeError) as error:
        raise HTTPException(
            status_code=422, detail=f"Malformed edit payload: {error}"
        ) from error

    detail = get_resume_detail(session, resume_id)
    return JSONResponse({"id": detail.id, "name": detail.name})


@router.get("/resumes/{resume_id}", response_class=HTMLResponse)
def resume_detail_page(
    resume_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Render one resume with its sections, tailor form, and export links."""
    detail = get_resume_detail(session, resume_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id} not found")

    jobs = list_jobs(session)
    return templates.TemplateResponse(
        request,
        "resume_detail.html",
        {
            "detail": detail,
            "jobs": jobs,
            "section_type": SectionType,
            "export_formats": EXPORT_FORMATS,
        },
    )


def _render_tailoring_result(
    request: Request,
    session: Session,
    *,
    base_resume_id: int,
    job_id: int,
) -> HTMLResponse:
    """Run one tailoring pass and render the shared result fragment."""
    try:
        variant = ResumeTailor().tailor_to_job(
            session, base_resume_id=base_resume_id, job_id=job_id
        )
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error

    detail = get_resume_detail(session, variant.id)
    kept_items = [item for section in detail.sections for item in section.items]
    fallback_items = [item for item in kept_items if item.score_is_fallback]

    return templates.TemplateResponse(
        request,
        "_resume_tailoring_result.html",
        {
            "variant": detail,
            "kept_item_count": len(kept_items),
            "fallback_item_count": len(fallback_items),
        },
    )


def _form_int(body: str, field: str) -> int:
    raw_value = parse_qs(body).get(field, [None])[0]
    try:
        return int(raw_value)
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400, detail=f"Invalid {field.replace('_', ' ')}"
        ) from error


@router.post("/resumes/{resume_id}/tailor", response_class=HTMLResponse)
async def tailor_resume(
    resume_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Tailor from the resume detail page: the job comes from the form."""
    job_id = _form_int((await request.body()).decode(), "job_id")
    return _render_tailoring_result(
        request, session, base_resume_id=resume_id, job_id=job_id
    )


@router.post("/jobs/{job_id}/tailor", response_class=HTMLResponse)
async def tailor_resume_for_job(
    job_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Tailor from the jobs list: the base resume comes from the form."""
    resume_id = _form_int((await request.body()).decode(), "resume_id")
    return _render_tailoring_result(
        request, session, base_resume_id=resume_id, job_id=job_id
    )


@router.get("/resumes/{resume_id}/export")
def export_resume(
    resume_id: int,
    format: str = "json",
    session: Session = Depends(get_session),
) -> Response:
    """Download the compiled resume in the requested format."""
    detail = get_resume_detail(session, resume_id)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Resume {resume_id} not found")

    if format not in EXPORT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown format {format!r}; expected one of {EXPORT_FORMATS}",
        )

    compiler = ResumeCompiler()
    filename = f"resume-{detail.name}"

    if format == "json":
        return JSONResponse(
            compiler.to_json(detail),
            headers=_attachment_header(f"{filename}.json"),
        )
    if format == "json_resume":
        return JSONResponse(
            compiler.to_json_resume(detail),
            headers=_attachment_header(f"{filename}.resume.json"),
        )
    if format == "html":
        # Rendered inline so the browser shows a preview instead of a download.
        return HTMLResponse(compiler.to_html(detail))

    try:
        pdf_bytes = compiler.to_pdf(detail)
    except ResumeExportError as error:
        raise HTTPException(status_code=501, detail=str(error)) from error
    return Response(
        pdf_bytes,
        media_type="application/pdf",
        headers=_attachment_header(f"{filename}.pdf"),
    )


def _attachment_header(filename: str) -> dict[str, str]:
    return {"Content-Disposition": f'attachment; filename="{filename}"'}
