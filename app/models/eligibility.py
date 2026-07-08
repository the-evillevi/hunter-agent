"""Eligibility result shapes for deterministic job filtering.

This module exists so hard pass/fail filtering (HNTR-49) has an explainable,
machine-readable result that the scoring pipeline (HNTR-11) can consume
without source-specific branches. Rejections always carry reason codes, and
data the listing simply does not have is reported as unknown instead of
being silently treated as a match or a rejection.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class EligibilityReasonCode(StrEnum):
    """Machine-readable reasons a job can be rejected."""

    excluded_keyword = "excluded_keyword"
    location_type_mismatch = "location_type_mismatch"


class UnknownField(StrEnum):
    """Constraints that could not be checked because the listing lacks data.

    Unknown never fabricates a result in either direction: the job stays
    eligible, and the unchecked constraint is surfaced for downstream
    warnings and audit.
    """

    salary = "salary"
    location_type = "location_type"


class EligibilityReason(BaseModel):
    """One explainable rejection reason."""

    model_config = ConfigDict(frozen=True)

    code: EligibilityReasonCode
    detail: str = Field(min_length=1)


class EligibilityResult(BaseModel):
    """Deterministic eligibility decision for one job against one profile."""

    model_config = ConfigDict(frozen=True)

    eligible: bool
    reasons: tuple[EligibilityReason, ...]
    unknowns: tuple[UnknownField, ...]
    profile_role_name: str
    algorithm_version: str
