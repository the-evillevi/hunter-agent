"""Opt-in scoring comparison harness (HNTR-1).

Runs every scoring layer over the labeled fixture and prints one table:
layer scores, aggregate, human label, and disagreements first. Deterministic
layers (eligibility, keyword) always run; the semantic and LLM layers join
only when the configured Ollama server answers, otherwise their columns say
SKIPPED. Run it with::

    HUNTER_RUN_SCORING_EVAL=1 uv run pytest tests/test_scoring_eval.py -s

It stays a test (not just a script) by asserting that the deterministic
layers are identical across two runs.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx
import pytest

from app.config import load_config
from app.services.ai.embeddings import OllamaEmbeddingsClient
from app.services.ai.ollama import OllamaCompletionProvider
from app.services.eligibility import check_eligibility
from app.services.keyword_scoring import score_job_keywords
from app.services.llm_scoring import LlmScoreLayer
from app.services.scoring_pipeline import (
    LAYER_WEIGHTS,
    KeywordScoreLayer,
    ScoreJobInput,
    ScoreLayerRegistry,
    score_job,
)
from app.services.semantic_scoring import SemanticScoreLayer
from scoring_eval import LABEL_CUTS, EvalJob, load_fixture


pytestmark = pytest.mark.skipif(
    os.environ.get("HUNTER_RUN_SCORING_EVAL") != "1",
    reason="opt-in harness: set HUNTER_RUN_SCORING_EVAL=1 (may call local models)",
)

OLLAMA_PROBE_TIMEOUT_SECONDS = 2.0
SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class EvalRow:
    """One job's scores across every layer, next to the human label."""

    job: EvalJob
    eligibility: str
    keyword: int
    semantic: str
    llm: str
    aggregate: int | None

    @property
    def aggregate_bucket(self) -> int:
        """Map the aggregate onto the human 0-2 scale for comparison."""
        if self.aggregate is None:
            return 0
        if self.aggregate < LABEL_CUTS["low_below"]:
            return 0
        if self.aggregate < LABEL_CUTS["high_at_least"]:
            return 1
        return 2

    @property
    def disagreement(self) -> int:
        return abs(self.aggregate_bucket - self.job.human_label)


def ollama_base_url_if_reachable() -> str | None:
    """One cheap probe decides whether model layers join this run."""
    base_url = str(load_config().ollama.base_url)
    try:
        httpx.get(base_url, timeout=OLLAMA_PROBE_TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return None
    return base_url


def build_registry(base_url: str | None) -> ScoreLayerRegistry:
    """Keyword always; semantic + LLM only when Ollama answered the probe."""
    registry = ScoreLayerRegistry()
    registry.register(
        KeywordScoreLayer(), weight=LAYER_WEIGHTS["keyword"], required=True
    )
    if base_url is not None:
        config = load_config()
        registry.register(
            SemanticScoreLayer(OllamaEmbeddingsClient(base_url)),
            weight=LAYER_WEIGHTS["semantic"],
        )
        registry.register(
            LlmScoreLayer(OllamaCompletionProvider(config.ollama, "scorer")),
            weight=LAYER_WEIGHTS["llm"],
        )
    return registry


def evaluate_job(job: EvalJob, profile, registry: ScoreLayerRegistry) -> EvalRow:
    """Run each layer directly, then the composed pipeline for the aggregate."""
    eligibility = check_eligibility(
        title=job.title,
        description=job.description,
        location=job.location,
        profile=profile,
    )
    keyword = score_job_keywords(job.title, job.description, profile)

    job_input = ScoreJobInput(
        title=job.title, description=job.description, location=job.location
    )
    result = asyncio.run(score_job(job_input, profile, registry=registry))

    layer_scores: dict[str, str] = {}
    for outcome in result.layer_outcomes:
        if outcome.status == "success" and outcome.result is not None:
            layer_scores[outcome.layer] = str(outcome.result.score)
        else:
            layer_scores[outcome.layer] = SKIPPED

    return EvalRow(
        job=job,
        eligibility="ok" if eligibility.eligible else "rejected",
        keyword=keyword.score,
        semantic=layer_scores.get("semantic", SKIPPED),
        llm=layer_scores.get("llm", SKIPPED),
        aggregate=result.score,
    )


def print_table(rows: list[EvalRow], *, models_available: bool) -> None:
    """Disagreements first, biggest gap on top, so they leap out."""
    ordered = sorted(rows, key=lambda row: (-row.disagreement, row.job.id))
    header = (
        f"{'job':<28} {'elig':<8} {'kw':>4} {'sem':>7} {'llm':>7} "
        f"{'agg':>4} {'human':>5} {'diff':>4}  note"
    )
    print()
    if not models_available:
        print("Ollama unreachable: semantic and llm columns are SKIPPED.")
    print(header)
    print("-" * len(header))
    for row in ordered:
        aggregate = "-" if row.aggregate is None else str(row.aggregate)
        print(
            f"{row.job.id:<28} {row.eligibility:<8} {row.keyword:>4} "
            f"{row.semantic:>7} {row.llm:>7} {aggregate:>4} "
            f"{row.job.human_label:>5} {row.disagreement:>4}  {row.job.note}"
        )


def test_layer_comparison_over_labeled_fixture() -> None:
    fixture = load_fixture()
    profile = fixture.profile.to_profile_detail()
    base_url = ollama_base_url_if_reachable()
    registry = build_registry(base_url)

    rows = [evaluate_job(job, profile, registry) for job in fixture.jobs]
    print_table(rows, models_available=base_url is not None)

    # Deterministic layers must be identical across runs: re-run
    # eligibility and keyword scoring and compare exactly.
    for job, row in zip(fixture.jobs, rows):
        again_eligibility = check_eligibility(
            title=job.title,
            description=job.description,
            location=job.location,
            profile=profile,
        )
        again_keyword = score_job_keywords(job.title, job.description, profile)
        assert ("ok" if again_eligibility.eligible else "rejected") == row.eligibility
        assert again_keyword.score == row.keyword
