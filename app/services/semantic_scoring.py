"""Optional embedding-similarity score layer.

Keyword scoring is transparent but misses semantically similar role
language ("ML engineer" vs "machine learning practitioner"). This layer
(HNTR-12) embeds a bounded job representation and a profile representation
through the local Ollama embeddings API and scores their cosine
similarity. Embedding similarity never executes instructions in job text,
so it sits behind the score-layer contract, not the prompt guard, and it
stays optional: failures degrade to a pipeline skip, never to a broken
deterministic score.
"""

import hashlib
import math

from app.models.config import ProfileConfig
from app.models.scoring import ScoreLayerResult
from app.services.ai.embeddings import OllamaEmbeddingsClient
from app.services.ai.errors import AIProviderError
from app.services.scoring_pipeline import ScoreJobInput, ScoreLayerUnavailableError


SEMANTIC_LAYER_NAME = "semantic"

# Bump when text building, similarity math, or normalization changes.
SEMANTIC_ALGORITHM_VERSION = "1"

# Bounded representations keep embedding inputs comparable and cheap; the
# nomic-embed-text context window also caps what is useful to send.
MAX_JOB_CHARS = 4000
MAX_PROFILE_CHARS = 1000


class SemanticScoreResult(ScoreLayerResult):
    """Semantic layer result keeping model identity and the raw similarity."""

    model: str
    similarity: float


def build_job_text(title: str | None, description: str | None) -> str:
    """One bounded text representing the job for embedding."""
    combined = " ".join(part for part in (title, description) if part)
    return combined[:MAX_JOB_CHARS]


def build_profile_text(profile: ProfileConfig) -> str:
    """One bounded text representing the target profile for embedding.

    Keywords carry most of the signal today; the role name anchors them.
    A richer structured representation stays an open follow-up question.
    """
    combined = f"{profile.role_name}: {', '.join(profile.keywords)}"
    return combined[:MAX_PROFILE_CHARS]


def cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Plain cosine similarity, written out for readability."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def similarity_to_score(similarity: float) -> int:
    """Deterministically map cosine similarity onto the 0-100 score range.

    Negative similarity clamps to 0: "actively dissimilar" and "unrelated"
    both mean "not a match" for ranking purposes.
    """
    return round(100 * min(1.0, max(0.0, similarity)))


class SemanticScoreLayer:
    """Optional score layer backed by Ollama embeddings.

    Implements the HNTR-11 ``ScoreLayer`` contract. The cache boundary is
    an in-memory dict owned by whoever constructs the layer (one scrape
    run shares one layer instance): keys are model plus text hash, so the
    profile text embeds once per run instead of once per job.
    """

    name = SEMANTIC_LAYER_NAME

    def __init__(
        self,
        embeddings: OllamaEmbeddingsClient,
        *,
        cache: dict[str, tuple[float, ...]] | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._cache = cache if cache is not None else {}

    async def score(
        self,
        job: ScoreJobInput,
        profile: ProfileConfig,
    ) -> SemanticScoreResult:
        """Embed job and profile texts and score their similarity."""
        job_text = build_job_text(job.title, job.description)
        profile_text = build_profile_text(profile)

        try:
            job_vector = await self._embed_cached(job_text)
            profile_vector = await self._embed_cached(profile_text)
        except AIProviderError as error:
            # Explicit degradation: the pipeline records a skip and keeps
            # deterministic scoring intact when the local server is down.
            raise ScoreLayerUnavailableError(
                f"semantic layer unavailable: {error}"
            ) from error

        similarity = cosine_similarity(job_vector, profile_vector)
        score = similarity_to_score(similarity)

        return SemanticScoreResult(
            layer=self.name,
            algorithm_version=SEMANTIC_ALGORITHM_VERSION,
            score=score,
            explanation=(
                f"Embedding similarity {similarity:.3f} against profile "
                f"{profile.role_name!r} using {self._embeddings.model}"
            ),
            model=self._embeddings.model,
            similarity=similarity,
        )

    async def _embed_cached(self, text: str) -> tuple[float, ...]:
        """Embed one text, reusing the run-scoped cache on repeats."""
        key = f"{self._embeddings.model}:{hashlib.sha256(text.encode()).hexdigest()}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        vector = (await self._embeddings.embed([text]))[0]
        self._cache[key] = vector
        return vector
