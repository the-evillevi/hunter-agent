"""Tests for the composed scoring pipeline.

Fake layers stand in for future semantic/LLM implementations so every case
runs offline and deterministically: composition behavior is what is under
test here, not any real matching logic.
"""

import asyncio
from collections.abc import Callable

import pytest

from app.models.config import ProfileConfig
from app.models.scoring import ScoreLayerResult
from app.services.scoring_pipeline import (
    KeywordScoreLayer,
    PIPELINE_VERSION,
    RequiredScoreLayerError,
    ScoreJobInput,
    ScoreLayerRegistry,
    ScoreLayerUnavailableError,
    WEIGHTS_VERSION,
    score_job,
)


MakeProfile = Callable[..., ProfileConfig]


class FakeScoreLayer:
    """Configurable stand-in for an optional model-backed score layer."""

    def __init__(
        self,
        name: str,
        score: int = 50,
        error: Exception | None = None,
    ) -> None:
        self.name = name
        self.calls = 0
        self._score = score
        self._error = error

    async def score(
        self,
        job: ScoreJobInput,
        profile: ProfileConfig,
    ) -> ScoreLayerResult:
        self.calls += 1
        if self._error is not None:
            raise self._error
        return ScoreLayerResult(
            layer=self.name,
            algorithm_version="fake",
            score=self._score,
            explanation=f"{self.name} fake score",
        )


def make_registry(*entries: tuple[FakeScoreLayer, float, bool]) -> ScoreLayerRegistry:
    """Build a registry with a required keyword layer plus the given fakes."""
    registry = ScoreLayerRegistry()
    registry.register(KeywordScoreLayer(), weight=1.0, required=True)
    for layer, weight, required in entries:
        registry.register(layer, weight=weight, required=required)
    return registry


def python_job() -> ScoreJobInput:
    """A job whose title matches the default profile keyword exactly."""
    return ScoreJobInput(
        title="Python Developer",
        description=None,
        location="Remote",
    )


def test_ineligible_job_is_rejected_without_running_layers(
    make_profile: MakeProfile,
) -> None:
    profile = make_profile(exclude_keywords=["blockchain"])
    fake = FakeScoreLayer("semantic")
    registry = make_registry((fake, 0.5, False))

    result = asyncio.run(
        score_job(
            ScoreJobInput(title="Blockchain Engineer", description=None),
            profile,
            registry=registry,
        )
    )

    assert result.status == "rejected"
    assert result.score is None
    assert result.layer_outcomes == ()
    assert fake.calls == 0
    assert "excluded_keyword" in result.explanation


def test_keyword_only_pipeline_scores_deterministically(
    make_profile: MakeProfile,
) -> None:
    registry = make_registry()

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    assert result.status == "scored"
    assert result.score == 100
    assert [outcome.layer for outcome in result.layer_outcomes] == ["keyword"]


def test_aggregate_is_weighted_mean_of_successful_layers(
    make_profile: MakeProfile,
) -> None:
    fake = FakeScoreLayer("semantic", score=40)
    registry = make_registry((fake, 1.0, False))

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    # keyword scores 100 (weight 1.0) and the fake scores 40 (weight 1.0).
    assert result.score == 70


def test_unavailable_optional_layer_is_skipped_with_warning(
    make_profile: MakeProfile,
) -> None:
    fake = FakeScoreLayer(
        "semantic",
        error=ScoreLayerUnavailableError("Ollama is not running"),
    )
    registry = make_registry((fake, 1.0, False))

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    semantic_outcome = result.layer_outcomes[1]
    assert semantic_outcome.status == "skip"
    assert semantic_outcome.failure_code == "unavailable"
    # Weights renormalize over successful layers, so keyword alone owns 100.
    assert result.score == 100
    assert any("semantic" in warning for warning in result.warnings)


def test_crashing_optional_layer_records_failure_and_pipeline_continues(
    make_profile: MakeProfile,
) -> None:
    fake = FakeScoreLayer("llm", error=RuntimeError("model returned garbage"))
    registry = make_registry((fake, 1.0, False))

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    llm_outcome = result.layer_outcomes[1]
    assert result.status == "scored"
    assert llm_outcome.status == "failure"
    assert llm_outcome.failure_code == "layer_error"
    assert "model returned garbage" in (llm_outcome.failure_detail or "")


def test_failing_required_layer_raises(make_profile: MakeProfile) -> None:
    registry = ScoreLayerRegistry()
    registry.register(
        FakeScoreLayer("keyword", error=RuntimeError("boom")),
        weight=1.0,
        required=True,
    )

    with pytest.raises(RequiredScoreLayerError):
        asyncio.run(score_job(python_job(), make_profile(), registry=registry))


def test_eligibility_unknowns_surface_in_result_and_warnings(
    make_profile: MakeProfile,
) -> None:
    profile = make_profile(salary_min=90000)
    registry = make_registry()

    result = asyncio.run(score_job(python_job(), profile, registry=registry))

    assert result.eligibility.unknowns
    assert any("salary" in warning for warning in result.warnings)


def test_layer_outcomes_preserve_registration_order_and_timing(
    make_profile: MakeProfile,
) -> None:
    first = FakeScoreLayer("semantic", score=10)
    second = FakeScoreLayer("llm", score=90)
    registry = make_registry((first, 0.5, False), (second, 1.0, False))

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    assert [outcome.layer for outcome in result.layer_outcomes] == [
        "keyword",
        "semantic",
        "llm",
    ]
    assert all(outcome.duration_ms >= 0 for outcome in result.layer_outcomes)


def test_result_carries_pipeline_and_weights_versions(
    make_profile: MakeProfile,
) -> None:
    registry = make_registry()

    result = asyncio.run(score_job(python_job(), make_profile(), registry=registry))

    assert result.pipeline_version == PIPELINE_VERSION
    assert result.weights_version == WEIGHTS_VERSION


def test_duplicate_layer_names_are_rejected() -> None:
    registry = ScoreLayerRegistry()
    registry.register(FakeScoreLayer("semantic"), weight=1.0)

    with pytest.raises(ValueError):
        registry.register(FakeScoreLayer("semantic"), weight=0.5)
