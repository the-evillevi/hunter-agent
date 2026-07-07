"""Resume management routes: browse, inspect, tailor, and export.

Full pages live at /resumes and /resumes/{id}; HTMX fragments live under
/resumes/partials/ and are excluded from the OpenAPI schema. Tailoring is a
mutation with a meaningful URL that returns an HTML fragment for the modal
target, following the existing applications routes.
"""

from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from sqlmodel import Session

from app.db.database import get_session
from app.models.resume import SectionType
from app.services.jobs import list_jobs
from app.services.resume_compiler import ResumeCompiler, ResumeExportError
from app.services.resume_crud import get_resume_detail, list_resumes
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


@router.post("/resumes/{resume_id}/tailor", response_class=HTMLResponse)
async def tailor_resume(
    resume_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Run one tailoring pass and render the result fragment."""
    body = (await request.body()).decode()
    form = parse_qs(body)
    raw_job_id = form.get("job_id", [None])[0]

    try:
        job_id = int(raw_job_id)
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Invalid job id") from error

    try:
        variant = ResumeTailor().tailor_to_job(
            session, base_resume_id=resume_id, job_id=job_id
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
