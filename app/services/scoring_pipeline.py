"""Scoring pipeline that composes eligibility and independent score layers.

This is the one orchestration boundary for job scoring (HNTR-11): hard
filters run first, then registered score layers run in order, and the
caller gets a single explainable result. Layers stay decoupled behind a
small protocol so semantic and LLM implementations (HNTR-12/50) can be
registered later without the pipeline learning provider specifics, and
local model availability never prevents deterministic scoring.
"""

import time
from dataclasses import dataclass
import math
from typing import Protocol

from app.models.scoring import JobScoreResult, LayerOutcome, ScoreLayerResult
from app.services.eligibility import EligibilityProfile, check_eligibility
from app.services.keyword_scoring import (
    KEYWORD_LAYER_NAME,
    KeywordProfile,
    score_job_keywords,
)


# Bump these when composition or weighting behavior changes, so persisted
# results (HNTR-10) can distinguish runs produced by different versions.
PIPELINE_VERSION = "1"
WEIGHTS_VERSION = "1"

# Layer weights are versioned code defaults, like the field weights inside
# the keyword layer: tuning them is an algorithm change, not configuration.
LAYER_WEIGHTS: dict[str, float] = {
    "keyword": 1.0,
    "semantic": 0.5,
    "llm": 1.0,
}

# Bounded failure detail keeps layer exceptions auditable without storing
# unbounded tracebacks or model output in results.
MAX_FAILURE_DETAIL_CHARS = 500


@dataclass(frozen=True)
class ScoreJobInput:
    """The normalized job fields every score layer may read."""

    title: str | None
    description: str | None
    location: str | None = None


class ScoreLayerUnavailableError(Exception):
    """Raised by a layer that cannot run right now (service down, missing model).

    Optional layers raising this are skipped with a warning instead of
    failing the pipeline, which is how "local model availability never
    prevents basic scoring" is enforced.
    """

    def __init__(self, message: str, *, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code


class RequiredScoreLayerError(Exception):
    """Raised when a required layer fails: the run cannot produce a score."""


class ScoringProfile(KeywordProfile, EligibilityProfile, Protocol):
    """Profile aggregate shared by eligibility and every score layer."""


class ScoreLayer(Protocol):
    """What the pipeline needs from any score layer implementation."""

    name: str

    async def score(
        self,
        job: ScoreJobInput,
        profile: ScoringProfile,
    ) -> ScoreLayerResult: ...


class KeywordScoreLayer:
    """Async adapter that lets deterministic keyword scoring join the pipeline."""

    name = KEYWORD_LAYER_NAME

    async def score(
        self,
        job: ScoreJobInput,
        profile: ScoringProfile,
    ) -> ScoreLayerResult:
        """Run the synchronous keyword scorer behind the async layer protocol."""
        return score_job_keywords(job.title, job.description, profile)


@dataclass(frozen=True)
class RegisteredScoreLayer:
    """One layer plus the composition policy the pipeline applies to it."""

    layer: ScoreLayer
    weight: float
    required: bool


class ScoreLayerRegistry:
    """Holds score layers in registration order, mirroring the source registry.

    Registration order is execution order, which keeps runs deterministic
    and lets tests register fakes without provider-specific branches.
    """

    def __init__(self) -> None:
        self._layers: list[RegisteredScoreLayer] = []

    def register(
        self,
        layer: ScoreLayer,
        *,
        weight: float,
        required: bool = False,
    ) -> None:
        """Add a layer to the pipeline with its weight and failure policy."""
        if not math.isfinite(weight) or weight <= 0:
            raise ValueError("score layer weight must be finite and positive")
        if not layer.name.strip():
            raise ValueError("score layer name must not be blank")
        if any(entry.layer.name == layer.name for entry in self._layers):
            raise ValueError(f"score layer {layer.name!r} is already registered")
        self._layers.append(
            RegisteredScoreLayer(layer=layer, weight=weight, required=required)
        )

    def resolve(self) -> list[RegisteredScoreLayer]:
        """Return registered layers in execution order."""
        return list(self._layers)


# The deterministic keyword layer is always available, so the default
# pipeline registers it as required; optional model layers join in later
# stories (HNTR-12/50) at import time of their own modules or app wiring.
default_score_layer_registry = ScoreLayerRegistry()
default_score_layer_registry.register(
    KeywordScoreLayer(),
    weight=LAYER_WEIGHTS["keyword"],
    required=True,
)


async def score_job(
    job: ScoreJobInput,
    profile: ScoringProfile,
    *,
    registry: ScoreLayerRegistry = default_score_layer_registry,
) -> JobScoreResult:
    """Score one job against one profile through eligibility and all layers.

    Ineligible jobs are rejected before any layer runs, so no model work is
    spent on hard mismatches. The aggregate is a weighted mean over the
    layers that succeeded, renormalized so a skipped optional layer lowers
    confidence in coverage (visible in warnings) without dragging the score
    toward zero.
    """
    eligibility = check_eligibility(
        title=job.title,
        description=job.description,
        location=job.location,
        profile=profile,
    )
    unknown_warnings = tuple(
        f"unchecked constraint: {unknown.value}" for unknown in eligibility.unknowns
    )

    if not eligibility.eligible:
        rejection_details = ", ".join(
            f"{reason.code.value} ({reason.detail})" for reason in eligibility.reasons
        )
        return JobScoreResult(
            status="rejected",
            eligibility=eligibility,
            warnings=unknown_warnings,
            explanation=f"Rejected by eligibility filters: {rejection_details}",
            pipeline_version=PIPELINE_VERSION,
            weights_version=WEIGHTS_VERSION,
        )

    entries = registry.resolve()
    outcomes: list[LayerOutcome] = []
    warnings = list(unknown_warnings)

    for entry in entries:
        outcome = await _run_layer(entry, job, profile)
        outcomes.append(outcome)
        if outcome.status == "skip":
            warnings.append(f"layer {outcome.layer} skipped: {outcome.failure_detail}")
        elif outcome.status == "failure":
            warnings.append(f"layer {outcome.layer} failed: {outcome.failure_detail}")

    # Warnings stay structured on their own field; the explanation only
    # narrates successful layer results, so callers never parse one out of
    # the other. At least one successful layer is required to emit a score:
    # a zero from "nothing ran" would be indistinguishable from a real 0.
    successes = [
        outcome
        for outcome in outcomes
        if outcome.status == "success" and outcome.result is not None
    ]
    if not successes:
        return JobScoreResult(
            status="failed",
            eligibility=eligibility,
            layer_outcomes=tuple(outcomes),
            warnings=tuple(warnings),
            explanation="No score layers succeeded, so no aggregate score exists",
            pipeline_version=PIPELINE_VERSION,
            weights_version=WEIGHTS_VERSION,
        )

    aggregate = _aggregate_score(outcomes, entries)
    explanation = "; ".join(
        f"{outcome.layer}: {outcome.result.explanation}"
        for outcome in successes
        if outcome.result is not None
    )

    return JobScoreResult(
        status="scored",
        eligibility=eligibility,
        score=aggregate,
        layer_outcomes=tuple(outcomes),
        warnings=tuple(warnings),
        explanation=explanation,
        pipeline_version=PIPELINE_VERSION,
        weights_version=WEIGHTS_VERSION,
    )


async def _run_layer(
    entry: RegisteredScoreLayer,
    job: ScoreJobInput,
    profile: ScoringProfile,
) -> LayerOutcome:
    """Run one layer and translate its outcome into the auditable shape."""
    started = time.perf_counter()
    try:
        result = await entry.layer.score(job, profile)
        if result.layer != entry.layer.name:
            raise ValueError(
                f"layer {entry.layer.name!r} returned result for {result.layer!r}"
            )
    except ScoreLayerUnavailableError as error:
        if entry.required:
            raise RequiredScoreLayerError(
                f"required layer {entry.layer.name!r} is unavailable: {error}"
            ) from error
        return LayerOutcome(
            layer=entry.layer.name,
            status="skip",
            duration_ms=_elapsed_ms(started),
            failure_code=error.code,
            failure_detail=_bounded_detail(str(error)),
        )
    except Exception as error:
        if entry.required:
            raise RequiredScoreLayerError(
                f"required layer {entry.layer.name!r} failed: {error}"
            ) from error
        return LayerOutcome(
            layer=entry.layer.name,
            status="failure",
            duration_ms=_elapsed_ms(started),
            failure_code="layer_error",
            failure_detail=_bounded_detail(str(error)),
        )

    return LayerOutcome(
        layer=entry.layer.name,
        status="success",
        result=result,
        duration_ms=_elapsed_ms(started),
    )


def _aggregate_score(
    outcomes: list[LayerOutcome],
    entries: list[RegisteredScoreLayer],
) -> int:
    """Combine successful layer scores into one bounded weighted mean.

    Weights are renormalized over the layers that actually succeeded, so
    the aggregate stays comparable whether or not optional layers ran.
    The caller guarantees at least one successful outcome exists.
    """
    weights_by_name = {entry.layer.name: entry.weight for entry in entries}
    weighted_sum = 0.0
    weight_total = 0.0
    for outcome in outcomes:
        if outcome.status != "success" or outcome.result is None:
            continue
        weight = weights_by_name[outcome.layer]
        weighted_sum += weight * outcome.result.score
        weight_total += weight

    return min(100, max(0, round(weighted_sum / weight_total)))


def _elapsed_ms(started: float) -> int:
    """Return non-negative elapsed wall time in whole milliseconds."""
    return max(0, round((time.perf_counter() - started) * 1000))


def _bounded_detail(detail: str) -> str:
    """Trim failure detail so results never carry unbounded error text."""
    return detail[:MAX_FAILURE_DETAIL_CHARS]
