"""Tests for deterministic job eligibility filters.

Every case runs purely in memory — eligibility must be reproducible and
explainable with no network, model, or database dependency.
"""

from app.models.config import ProfileConfig
from app.models.eligibility import EligibilityReasonCode, UnknownField
from app.services.eligibility import check_eligibility, infer_location_type


def make_profile(
    *,
    salary_min: int = 0,
    location_type: str | list[str] = "remote",
    exclude_keywords: list[str] | None = None,
) -> ProfileConfig:
    """Build a minimal valid profile for eligibility tests."""
    return ProfileConfig(
        role_name="Test Role",
        active=True,
        match_threshold=80,
        salary_min=salary_min,
        location_type=location_type,
        keywords=["Python"],
        exclude_keywords=exclude_keywords or [],
    )


def test_clean_job_passes_with_no_reasons() -> None:
    profile = make_profile(location_type=["remote", "hybrid"])

    result = check_eligibility(
        title="Python Developer",
        description="Build APIs.",
        location="Remote",
        profile=profile,
    )

    assert result.eligible
    assert result.reasons == ()
    assert result.unknowns == ()
    assert result.profile_role_name == "Test Role"


def test_excluded_keyword_in_title_rejects_with_reason() -> None:
    profile = make_profile(exclude_keywords=["blockchain"])

    result = check_eligibility(
        title="Blockchain Engineer",
        description=None,
        location="Remote",
        profile=profile,
    )

    assert not result.eligible
    assert result.reasons[0].code == EligibilityReasonCode.excluded_keyword
    assert result.reasons[0].detail == "blockchain"


def test_excluded_keyword_in_description_rejects() -> None:
    profile = make_profile(exclude_keywords=["security clearance"])

    result = check_eligibility(
        title="Python Developer",
        description="Requires an active security clearance.",
        location="Remote",
        profile=profile,
    )

    assert not result.eligible


def test_excluded_keyword_does_not_match_partial_words() -> None:
    profile = make_profile(exclude_keywords=["Java"])

    result = check_eligibility(
        title="JavaScript Developer",
        description="JavaScript only.",
        location="Remote",
        profile=profile,
    )

    assert result.eligible


def test_multiple_reasons_are_all_collected() -> None:
    profile = make_profile(location_type="remote", exclude_keywords=["gaming"])

    result = check_eligibility(
        title="Gaming Backend Developer",
        description=None,
        location="Onsite - London",
        profile=profile,
    )

    codes = {reason.code for reason in result.reasons}
    assert not result.eligible
    assert codes == {
        EligibilityReasonCode.excluded_keyword,
        EligibilityReasonCode.location_type_mismatch,
    }


def test_salary_floor_is_unknown_until_jobs_carry_salary_data() -> None:
    with_floor = check_eligibility(
        title="Python Developer",
        description=None,
        location="Remote",
        profile=make_profile(salary_min=90000),
    )
    disabled = check_eligibility(
        title="Python Developer",
        description=None,
        location="Remote",
        profile=make_profile(salary_min=0),
    )

    assert with_floor.eligible
    assert UnknownField.salary in with_floor.unknowns
    assert UnknownField.salary not in disabled.unknowns


def test_disallowed_location_type_rejects() -> None:
    profile = make_profile(location_type="remote")

    result = check_eligibility(
        title="Python Developer",
        description=None,
        location="On-site (Berlin)",
        profile=profile,
    )

    assert not result.eligible
    assert result.reasons[0].code == EligibilityReasonCode.location_type_mismatch
    assert result.reasons[0].detail == "onsite"


def test_uninferable_location_is_unknown_not_rejected() -> None:
    profile = make_profile(location_type="remote")

    result = check_eligibility(
        title="Python Developer",
        description=None,
        location="CDMX",
        profile=profile,
    )

    assert result.eligible
    assert UnknownField.location_type in result.unknowns


def test_ambiguous_location_counts_as_unknown() -> None:
    assert infer_location_type("Hybrid or remote") is None
    assert infer_location_type(None) is None
    assert infer_location_type("Work from home") == "remote"
    assert infer_location_type("In-office, NYC") == "onsite"


def test_identical_inputs_return_identical_results() -> None:
    profile = make_profile(salary_min=50000, exclude_keywords=["web3"])

    first = check_eligibility(
        title="Web3 Developer",
        description="DeFi platform.",
        location="Remote",
        profile=profile,
    )
    second = check_eligibility(
        title="Web3 Developer",
        description="DeFi platform.",
        location="Remote",
        profile=profile,
    )

    assert first == second
