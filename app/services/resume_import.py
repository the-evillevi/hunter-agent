"""Import cvs/resume.json into resume profile/section/item rows.

The canonical master resume lives in ``cvs/resume.json`` so it can be
reviewed, diffed, and versioned like code. This module validates that file
with Pydantic at the boundary and writes one ResumeProfile with its sections
and items in a single transaction. Run it directly to (re)import the master
resume:

    uv run python -m app.services.resume_import
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator
from sqlmodel import Session

from app.config import PROJECT_ROOT
from app.models.resume import ResumeProfile, SectionType
from app.services.resume_crud import add_item, add_section, create_resume_profile


DEFAULT_RESUME_JSON_PATH = PROJECT_ROOT / "cvs" / "resume.json"


class ResumeSectionDocument(BaseModel):
    """One section block inside resume.json."""

    model_config = ConfigDict(extra="forbid")

    type: SectionType
    title: str
    items: list[dict]

    @field_validator("type")
    @classmethod
    def reject_basics_section(cls, value: SectionType) -> SectionType:
        """Basics lives in the top-level key; a second copy here would import
        two basics sections and confuse every downstream consumer."""
        if value == SectionType.basics:
            raise ValueError(
                "Put contact info in the top-level 'basics' key,"
                " not in a 'basics' section"
            )
        return value


class ResumeDocument(BaseModel):
    """Validated shape of the whole resume.json file.

    ``basics`` (contact info, availability) is kept as a plain dict and
    stored as the single item of a ``basics`` section, so every fact in the
    database lives in the same sections/items shape.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    basics: dict
    sections: list[ResumeSectionDocument]


def load_resume_document(path: Path = DEFAULT_RESUME_JSON_PATH) -> ResumeDocument:
    """Read and validate resume.json, failing early on shape mistakes."""
    with path.open(encoding="utf-8") as resume_file:
        raw_document = json.load(resume_file)
    return ResumeDocument.model_validate(raw_document)


def import_resume(session: Session, document: ResumeDocument) -> ResumeProfile:
    """Write one validated resume document as a new master profile.

    The whole graph is committed once at the end, so a failure part-way
    through leaves no orphaned profile behind after the rollback.
    """
    try:
        profile = create_resume_profile(session, name=document.name)

        basics_section = add_section(
            session,
            profile_id=profile.id,
            section_type=SectionType.basics,
            title="Basics",
            order_idx=0,
        )
        add_item(session, section_id=basics_section.id, content=document.basics)

        for section_idx, section_document in enumerate(document.sections, start=1):
            section = add_section(
                session,
                profile_id=profile.id,
                section_type=section_document.type,
                title=section_document.title,
                order_idx=section_idx,
            )
            for item_idx, item_content in enumerate(section_document.items):
                add_item(
                    session,
                    section_id=section.id,
                    content=item_content,
                    order_idx=item_idx,
                )

        session.commit()
    except Exception:
        session.rollback()
        raise

    session.refresh(profile)
    return profile


def main() -> None:
    """Import the master resume into the local development database."""
    from app.db.database import engine

    document = load_resume_document()
    with Session(engine) as session:
        profile = import_resume(session, document)
        print(f"Imported resume profile #{profile.id} ({profile.name!r})")


if __name__ == "__main__":
    main()
