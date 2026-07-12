"""Structured boundaries for generator-critic resume tailoring."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.resume import SectionType


class GeneratedResumeItem(BaseModel):
    """One rewritten item tied to a real source resume fact."""

    model_config = ConfigDict(extra="forbid")

    source_item_id: int = Field(gt=0)
    # OpenAI strict structured outputs cannot represent an arbitrary JSON
    # object. The model therefore returns a JSON-encoded object string, which
    # this boundary validates before any content reaches persistence.
    content_json: str = Field(min_length=2)

    @field_validator("content_json")
    @classmethod
    def content_must_be_a_nonempty_json_object(cls, content_json: str) -> str:
        try:
            content = json.loads(content_json)
        except json.JSONDecodeError as error:
            raise ValueError("content_json must contain valid JSON") from error
        if not isinstance(content, dict) or not content:
            raise ValueError("content_json must contain a nonempty JSON object")
        return content_json

    def content_dict(self) -> dict[str, Any]:
        """Return the already-validated resume item payload."""
        return json.loads(self.content_json)


class GeneratedResumeSection(BaseModel):
    """One generated section containing traceable resume items."""

    model_config = ConfigDict(extra="forbid")

    section_type: SectionType
    title: str = Field(min_length=1)
    items: list[GeneratedResumeItem] = Field(min_length=1)


class GeneratedResumeDraft(BaseModel):
    """The only shape a generator may use to propose resume content."""

    model_config = ConfigDict(extra="forbid")

    sections: list[GeneratedResumeSection] = Field(min_length=1)


class ResumeCritique(BaseModel):
    """Structured feedback that cannot directly write into the variant."""

    model_config = ConfigDict(extra="forbid")

    fit_summary: str = Field(min_length=1)
    missing_evidence: list[str]
    overclaims: list[str]
    required_changes: list[str]

    @property
    def needs_revision(self) -> bool:
        return bool(self.missing_evidence or self.overclaims or self.required_changes)

    def audit_summary(self) -> str:
        """Return compact, non-verbatim critique metadata for persistence."""
        return (
            f"{self.fit_summary} "
            f"Missing evidence: {len(self.missing_evidence)}; "
            f"overclaims: {len(self.overclaims)}; "
            f"required changes: {len(self.required_changes)}."
        )
