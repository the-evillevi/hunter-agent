"""Score-layer result contracts shared by scoring implementations.

This module exists so every scoring layer (keyword now; semantic and LLM
later, see HNTR-11/12/50) returns the same explainable shape: a bounded
score, the algorithm identity that produced it, and a human-readable
explanation. Keeping the contract in `app/models/` mirrors how config and
job shapes are defined once and consumed by services.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator, SerializeAsAny

from app.models.eligibility import EligibilityResult


class ScoreLayerResult(BaseModel):
    """Common result shape every score layer must return.

    HNTR-11 composes these into one aggregate job score; persistence
    (HNTR-10) stores them for audit. Layers may subclass to add
    layer-specific detail fields, but the shared fields stay stable.
    """

    model_config = ConfigDict(frozen=True)

    layer: str = Field(min_length=1)
    algorithm_version: str = Field(min_length=1)
    score: int = Field(ge=0, le=100)
    explanation: str


class KeywordScoreResult(ScoreLayerResult):
    """Keyword-layer result with per-field detail for explainability.

    Title and description contributions stay separately visible so the
    review UI and the evaluation harness can show *where* a job matched,
    not just how well.
    """

    title_score: int = Field(ge=0, le=100)
    description_score: int = Field(ge=0, le=100)
    matched_title_terms: tuple[str, ...]
    matched_description_terms: tuple[str, ...]
    missing_terms: tuple[str, ...]
    excluded_terms_found: tuple[str, ...]


class SemanticScoreResult(ScoreLayerResult):
    """Semantic-layer result keeping model identity and the raw similarity."""

    model: str = Field(min_length=1)
    similarity: float = Field(ge=-1, le=1, allow_inf_nan=False)


class LlmScoreResult(ScoreLayerResult):
    """LLM-layer result keeping model, prompt, and safety audit metadata."""

    model: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    attempts: int = Field(ge=1)
    guard_flag_codes: tuple[str, ...]


class LayerOutcome(BaseModel):
    """What happened to one registered layer during a pipeline run.

    Success wraps the layer's result; skip records an unavailable optional
    layer; failure records an unexpected layer error. Persistence (HNTR-10)
    stores exactly this shape, so failures stay auditable after the run.
    """

    model_config = ConfigDict(frozen=True)

    layer: str
    status: Literal["success", "skip", "failure"]
    result: SerializeAsAny[ScoreLayerResult] | None = None
    duration_ms: int = Field(ge=0)
    failure_code: str | None = None
    failure_detail: str | None = None

    @model_validator(mode="after")
    def success_must_carry_a_result(self) -> "LayerOutcome":
        """A success outcome without a result would persist as a hollow row."""
        if self.status == "success" and self.result is None:
            raise ValueError("a successful layer outcome must include its result")
        return self


class JobScoreResult(BaseModel):
    """One job's complete, explainable scoring outcome for one profile.

    Rejected jobs carry the eligibility decision and no layer work; scored
    jobs carry a bounded aggregate plus every per-layer outcome so UI and
    persistence callers never need to re-run scoring to explain a number.
    A failed run means eligibility passed but no score layer succeeded, so
    no aggregate exists — that is different from legitimately scoring 0.
    """

    model_config = ConfigDict(frozen=True)

    status: Literal["rejected", "scored", "failed"]
    eligibility: EligibilityResult
    score: int | None = Field(default=None, ge=0, le=100)
    layer_outcomes: tuple[LayerOutcome, ...] = ()
    warnings: tuple[str, ...] = ()
    explanation: str
    pipeline_version: str
    weights_version: str

    @model_validator(mode="after")
    def status_and_score_must_agree(self) -> "JobScoreResult":
        """Scored means a real aggregate exists; anything else means none does.

        This is the invariant persistence relies on: a scored run always
        caches a numeric aggregate, and a rejected or failed run never
        writes one.
        """
        if self.status == "scored":
            if self.score is None:
                raise ValueError("a scored result must carry an aggregate score")
            if not self.layer_outcomes:
                raise ValueError("a scored result must carry its layer outcomes")
        elif self.score is not None:
            raise ValueError("only scored results may carry an aggregate score")
        return self
