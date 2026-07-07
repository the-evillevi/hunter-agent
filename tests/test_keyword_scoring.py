"""Tests for deterministic keyword scoring.

Every case runs purely in memory: no Ollama, no network, no database. That
is the core promise of this layer — identical inputs always produce the
same bounded score and explanation.
"""

from app.models.config import ProfileConfig
from app.services.keyword_scoring import (
    DESCRIPTION_WEIGHT,
    TITLE_WEIGHT,
    score_job_keywords,
)


def make_profile(
    keywords: list[str],
    exclude_keywords: list[str] | None = None,
) -> ProfileConfig:
    """Build a minimal valid profile for scoring tests."""
    return ProfileConfig(
        role_name="Test Role",
        active=True,
        match_threshold=80,
        salary_min=0,
        location_type="remote",
        keywords=keywords,
        exclude_keywords=exclude_keywords or [],
    )


def test_exact_term_in_title_scores_full_weight() -> None:
    profile = make_profile(["Python"])

    result = score_job_keywords("Python Developer", "", profile)

    assert result.score == 100
    assert result.matched_title_terms == ("Python",)
    assert result.missing_terms == ()


def test_identical_inputs_return_identical_results() -> None:
    profile = make_profile(["Python", "Django", "REST API"])
    title = "Senior Python Engineer"
    description = "Build REST APIs with Django."

    first = score_job_keywords(title, description, profile)
    second = score_job_keywords(title, description, profile)

    assert first == second


def test_multi_word_phrase_matches_across_punctuation() -> None:
    profile = make_profile(["machine learning engineer"])

    result = score_job_keywords("Machine-Learning Engineer (Remote)", None, profile)

    assert result.matched_title_terms == ("machine learning engineer",)


def test_partial_word_does_not_match() -> None:
    profile = make_profile(["Java"])

    result = score_job_keywords("JavaScript Developer", "We use JavaScript.", profile)

    assert result.score == 0
    assert result.missing_terms == ("Java",)


def test_hyphen_and_collapsed_variants_match_each_other() -> None:
    profile = make_profile(["full-stack", "Node.js"])

    result = score_job_keywords("Fullstack NodeJS Developer", None, profile)

    assert set(result.matched_title_terms) == {"full-stack", "Node.js"}


def test_symbol_keywords_survive_normalization() -> None:
    profile = make_profile(["C++", "C#"])

    result = score_job_keywords("C++ / C# Systems Developer", None, profile)

    assert result.score == 100


def test_title_match_outweighs_description_match() -> None:
    profile = make_profile(["Python"])

    in_title = score_job_keywords("Python Developer", "", profile)
    in_description = score_job_keywords("Backend Developer", "Python required", profile)

    assert in_title.score > in_description.score
    assert in_description.score == round(100 * DESCRIPTION_WEIGHT / TITLE_WEIGHT)
    assert in_description.matched_description_terms == ("Python",)
    assert in_description.title_score == 0
    assert in_description.description_score == 100


def test_empty_and_missing_text_produce_valid_unscored_result() -> None:
    profile = make_profile(["Python", "Django"])

    result = score_job_keywords(None, None, profile)

    assert result.score == 0
    assert result.title_score == 0
    assert result.description_score == 0
    assert result.missing_terms == ("Python", "Django")


def test_excluded_terms_are_reported_but_do_not_change_score() -> None:
    profile = make_profile(["Python"], exclude_keywords=["blockchain"])

    with_excluded = score_job_keywords(
        "Python Developer", "Blockchain startup", profile
    )
    without_excluded = score_job_keywords("Python Developer", "Web startup", profile)

    assert with_excluded.excluded_terms_found == ("blockchain",)
    assert with_excluded.score == without_excluded.score
    assert "blockchain" in with_excluded.explanation


def test_case_insensitive_matching() -> None:
    profile = make_profile(["pYtHoN"])

    result = score_job_keywords("PYTHON developer", None, profile)

    assert result.score == 100


def test_explanation_counts_matches_per_field() -> None:
    profile = make_profile(["Python", "Django", "Kubernetes"])

    result = score_job_keywords("Python Developer", "Django experience", profile)

    assert "2/3" in result.explanation
    assert "1 in title" in result.explanation
    assert "1 in description" in result.explanation
