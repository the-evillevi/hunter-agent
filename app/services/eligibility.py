"""Deterministic job eligibility filters.

Hard constraints run before any relevance scoring (HNTR-49): a job that
trips an exclusion keyword or a disallowed location type is rejected with
explainable reasons, so an attractive aggregate score can never hide a hard
mismatch and no model work is wasted on it. This service has no network,
model, or persistence dependency.
"""

from app.models.config import ProfileConfig
from app.models.eligibility import (
    EligibilityReason,
    EligibilityReasonCode,
    EligibilityResult,
    UnknownField,
)
from app.services.keyword_scoring import FieldIndex


# Bump this when filtering behavior changes, so persisted results (HNTR-10)
# can distinguish decisions produced by different algorithm versions.
ELIGIBILITY_ALGORITHM_VERSION = "1"

# Free-text location names are the only location signal jobs carry today.
# Each phrase maps to a profile location type when it appears as whole
# tokens; FieldIndex also matches hyphen variants ("on-site" == "onsite").
LOCATION_TYPE_PHRASES: dict[str, tuple[str, ...]] = {
    "remote": ("remote", "work from home", "wfh", "anywhere"),
    "hybrid": ("hybrid",),
    "onsite": ("onsite", "on site", "in office", "in person"),
}


def allowed_location_types(profile: ProfileConfig) -> frozenset[str]:
    """Normalize the profile's single-or-list location_type field."""
    if isinstance(profile.location_type, str):
        return frozenset((profile.location_type,))
    return frozenset(profile.location_type)


def infer_location_type(location: str | None) -> str | None:
    """Read a location type out of a free-text location name, if it names one.

    Returns None when the text names no type ("CDMX") or is ambiguous
    ("hybrid or remote") — the caller must treat None as unknown, never as
    a match or a mismatch.
    """
    index = FieldIndex(location)
    inferred = {
        location_type
        for location_type, phrases in LOCATION_TYPE_PHRASES.items()
        if any(index.contains(phrase) for phrase in phrases)
    }
    if len(inferred) == 1:
        return next(iter(inferred))
    return None


def check_eligibility(
    *,
    title: str | None,
    description: str | None,
    location: str | None,
    profile: ProfileConfig,
) -> EligibilityResult:
    """Decide whether one job passes a profile's hard constraints.

    All violated constraints are collected instead of short-circuiting, so
    a rejection explains everything that is wrong with the job at once.
    """
    reasons: list[EligibilityReason] = []
    unknowns: list[UnknownField] = []

    # Exclusion keywords are hard rejections wherever they appear; the
    # keyword layer only reports them, this filter owns the decision.
    title_index = FieldIndex(title)
    description_index = FieldIndex(description)
    for term in profile.exclude_keywords:
        if title_index.contains(term) or description_index.contains(term):
            reasons.append(
                EligibilityReason(
                    code=EligibilityReasonCode.excluded_keyword,
                    detail=term,
                )
            )

    # Listings carry no structured salary today, so a configured salary
    # floor can only be surfaced as unchecked; salary_min == 0 means the
    # filter is disabled and there is nothing left unchecked.
    if profile.salary_min > 0:
        unknowns.append(UnknownField.salary)

    inferred_location_type = infer_location_type(location)
    if inferred_location_type is None:
        unknowns.append(UnknownField.location_type)
    elif inferred_location_type not in allowed_location_types(profile):
        reasons.append(
            EligibilityReason(
                code=EligibilityReasonCode.location_type_mismatch,
                detail=inferred_location_type,
            )
        )

    return EligibilityResult(
        eligible=not reasons,
        reasons=tuple(reasons),
        unknowns=tuple(unknowns),
        profile_role_name=profile.role_name,
        algorithm_version=ELIGIBILITY_ALGORITHM_VERSION,
    )
