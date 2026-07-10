"""Compile a resume profile into distributable formats.

Takes the ResumeDetail display shape produced by resume_crud and turns it
into four outputs: the project's own JSON schema, the JSON Resume standard
(https://jsonresume.org), standalone HTML, and PDF. Keeping every format
behind one class means routes and future exporters never touch section
mapping logic directly.
"""

import json

import jsonschema
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import PROJECT_ROOT
from app.models.resume import ResumeDetail, ResumeSectionDetail, SectionType


# WeasyPrint output beyond this is almost certainly a template bug, and many
# application portals reject large uploads anyway.
MAX_PDF_BYTES = 5 * 1024 * 1024


class ResumeExportError(Exception):
    """Raised when an export format cannot be produced."""


# Module-level environment so the template cache is shared across compiler
# instances, matching the module-level Jinja2Templates pattern in app/routes.
_JINJA_ENVIRONMENT = Environment(
    loader=FileSystemLoader(PROJECT_ROOT / "app" / "templates"),
    autoescape=select_autoescape(["html"]),
)

# Committed copy of https://jsonresume.org/schema/ v1.0.0, so exports are
# validated against the exact spec version the mappers target.
_JSON_RESUME_SCHEMA = json.loads(
    (PROJECT_ROOT / "app" / "schemas" / "json_resume_schema.json").read_text(
        encoding="utf-8"
    )
)


class ResumeCompiler:
    """Renders one ResumeDetail into JSON, JSON Resume, HTML, or PDF."""

    def __init__(self) -> None:
        self._jinja = _JINJA_ENVIRONMENT

    def to_json(
        self,
        detail: ResumeDetail,
        *,
        sections: set[SectionType] | None = None,
    ) -> dict:
        """Return the full custom schema, including scores and lineage."""
        payload = detail.model_dump(mode="json", exclude={"sections"})
        payload["sections"] = [
            section.model_dump(mode="json")
            for section in _filtered_sections(detail, sections)
        ]
        return payload

    def to_json_resume(
        self,
        detail: ResumeDetail,
        *,
        sections: set[SectionType] | None = None,
    ) -> dict:
        """Map the internal sections onto the JSON Resume v1 schema."""
        document: dict = {
            "$schema": (
                "https://raw.githubusercontent.com/jsonresume/resume-schema"
                "/v1.0.0/schema.json"
            ),
            "basics": {},
            "work": [],
            "education": [],
            "skills": [],
            "projects": [],
            "publications": [],
            "certificates": [],
        }

        for section in _filtered_sections(detail, sections):
            mapper = _JSON_RESUME_SECTION_MAPPERS.get(section.section_type)
            if mapper is not None:
                mapper(section, document)

        try:
            jsonschema.validate(document, _JSON_RESUME_SCHEMA)
        except jsonschema.ValidationError as error:
            raise ResumeExportError(
                f"JSON Resume export failed v1.0.0 schema validation: {error.message}"
            ) from error

        return document

    def to_html(
        self,
        detail: ResumeDetail,
        *,
        sections: set[SectionType] | None = None,
        include_scores: bool = False,
    ) -> str:
        """Render standalone HTML with inline CSS, safe to email or print."""
        template = self._jinja.get_template("resume_export.html")
        return template.render(
            detail=detail,
            sections=_filtered_sections(detail, sections),
            basics=_basics_content(detail),
            include_scores=include_scores,
            section_type=SectionType,
        )

    def to_pdf(
        self,
        detail: ResumeDetail,
        *,
        sections: set[SectionType] | None = None,
    ) -> bytes:
        """Render the HTML export to PDF via WeasyPrint."""
        # WeasyPrint needs both the Python package and native Pango/GObject
        # libraries (`brew install pango` on macOS); either can be missing.
        try:
            import weasyprint
        except (ImportError, OSError) as error:
            raise ResumeExportError(
                "PDF export requires weasyprint and its native libraries;"
                " install with `uv add weasyprint` and `brew install pango`"
            ) from error

        html = self.to_html(detail, sections=sections)
        try:
            pdf_bytes = weasyprint.HTML(string=html).write_pdf()
        except OSError as error:
            raise ResumeExportError(
                "PDF rendering failed: weasyprint's native libraries are"
                " missing or broken (`brew install pango` on macOS)"
            ) from error
        if len(pdf_bytes) > MAX_PDF_BYTES:
            raise ResumeExportError(
                f"PDF export is {len(pdf_bytes)} bytes, above the"
                f" {MAX_PDF_BYTES} byte limit"
            )
        return pdf_bytes


def _filtered_sections(
    detail: ResumeDetail,
    sections: set[SectionType] | None,
) -> list[ResumeSectionDetail]:
    """Basics always survives filtering: exports need contact info."""
    if sections is None:
        return detail.sections
    return [
        section
        for section in detail.sections
        if section.section_type in sections
        or section.section_type == SectionType.basics
    ]


def _basics_content(detail: ResumeDetail) -> dict:
    for section in detail.sections:
        if section.section_type == SectionType.basics and section.items:
            return section.items[0].content
    return {}


def _map_basics(section: ResumeSectionDetail, document: dict) -> None:
    # Update in place: _map_summary may already have written basics.summary,
    # and JSON Resume v1 has no availability field, so that data stays in the
    # custom-schema export only.
    content = section.items[0].content if section.items else {}
    document["basics"].update(
        {
            "name": content.get("name", ""),
            "label": content.get("label", ""),
            "email": content.get("email", ""),
            "phone": content.get("phone", ""),
            "profiles": content.get("profiles", []),
        }
    )


def _map_summary(section: ResumeSectionDetail, document: dict) -> None:
    if section.items:
        document["basics"]["summary"] = section.items[0].content.get("text", "")


def _map_experience(section: ResumeSectionDetail, document: dict) -> None:
    for item in section.items:
        content = item.content
        entry = {
            "name": content.get("company", ""),
            "position": content.get("position", ""),
            "location": content.get("location", ""),
            "highlights": content.get("highlights", []),
        }
        # The schema requires iso8601 values when a date key is present, so
        # facts without dates must omit the keys rather than send "".
        for source_key, target_key in (
            ("start_date", "startDate"),
            ("end_date", "endDate"),
        ):
            if content.get(source_key):
                entry[target_key] = content[source_key]
        document["work"].append(entry)


def _map_education(section: ResumeSectionDetail, document: dict) -> None:
    for item in section.items:
        content = item.content
        entry = {
            "institution": content.get("institution", ""),
            "studyType": content.get("degree", ""),
        }
        # Optional JSON Resume fields, included only when the fact has them.
        for source_key, target_key in (
            ("area", "area"),
            ("start_date", "startDate"),
            ("end_date", "endDate"),
        ):
            if content.get(source_key):
                entry[target_key] = content[source_key]
        document["education"].append(entry)


def _map_skills(section: ResumeSectionDetail, document: dict) -> None:
    for item in section.items:
        content = item.content
        document["skills"].append(
            {
                "name": content.get("category", ""),
                "keywords": content.get("keywords", []),
            }
        )


# Items in a projects section with this exact (lowercase) kind value are
# routed to JSON Resume's publications list instead of projects.
PUBLICATION_KIND = "publication"


def _map_projects(section: ResumeSectionDetail, document: dict) -> None:
    for item in section.items:
        content = item.content
        if content.get("kind") == PUBLICATION_KIND:
            document["publications"].append(
                {
                    "name": content.get("name", ""),
                    "publisher": content.get("publisher", ""),
                    "summary": content.get("description", ""),
                }
            )
        else:
            document["projects"].append(
                {
                    "name": content.get("name", ""),
                    "description": content.get("description", ""),
                }
            )


def _map_certifications(section: ResumeSectionDetail, document: dict) -> None:
    for item in section.items:
        content = item.content
        document["certificates"].append(
            {
                "name": content.get("name", ""),
                "issuer": content.get("issuer", ""),
            }
        )


_JSON_RESUME_SECTION_MAPPERS = {
    SectionType.basics: _map_basics,
    SectionType.summary: _map_summary,
    SectionType.experience: _map_experience,
    SectionType.education: _map_education,
    SectionType.skills: _map_skills,
    SectionType.projects: _map_projects,
    SectionType.certifications: _map_certifications,
}
