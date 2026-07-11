"""Tests for the semantic scoring layer.

A fake embeddings client returns canned vectors, so similarity math,
caching, bounding, and degradation are all exercised without a live
Ollama server, model downloads, or network access.
"""

import asyncio
from collections.abc import Callable
import json

import httpx
import pytest

from app.models.scoring import SemanticScoreResult
from app.services.ai.embeddings import OllamaEmbeddingsClient
from app.services.ai.errors import AIConnectError, AIHTTPError, AIResponseError
from app.services.profiles import ProfileDetail
from app.services.scoring_pipeline import (
    KeywordScoreLayer,
    ScoreJobInput,
    ScoreLayerRegistry,
    ScoreLayerUnavailableError,
    score_job,
)
from app.services.semantic_scoring import (
    MAX_JOB_CHARS,
    MAX_PROFILE_CHARS,
    SemanticScoreLayer,
    build_job_text,
    build_profile_text,
    cosine_similarity,
    similarity_to_score,
)


MakeProfile = Callable[..., ProfileDetail]


class FakeEmbeddingsClient:
    """Returns canned vectors keyed by exact input text."""

    model = "fake-embed"

    def __init__(
        self,
        vectors: dict[str, tuple[float, ...]] | None = None,
        default: tuple[float, ...] = (1.0, 0.0),
        error: Exception | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._vectors = vectors or {}
        self._default = default
        self._error = error

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        self.calls.append(texts)
        if self._error is not None:
            raise self._error
        return [self._vectors.get(text, self._default) for text in texts]


def make_layer(client: FakeEmbeddingsClient) -> SemanticScoreLayer:
    return SemanticScoreLayer(client)


def run_score(layer: SemanticScoreLayer, profile: ProfileDetail):
    job = ScoreJobInput(title="Python Developer", description="Build APIs.")
    return asyncio.run(layer.score(job, profile))


def test_identical_vectors_score_100(make_profile: MakeProfile) -> None:
    result = run_score(make_layer(FakeEmbeddingsClient()), make_profile())

    assert result.score == 100
    assert result.layer == "semantic"
    assert result.model == "fake-embed"
    assert result.similarity == pytest.approx(1.0)


def test_orthogonal_vectors_score_0(make_profile: MakeProfile) -> None:
    profile = make_profile()
    client = FakeEmbeddingsClient(
        vectors={build_profile_text(profile): (0.0, 1.0)},
        default=(1.0, 0.0),
    )

    result = run_score(make_layer(client), profile)

    assert result.score == 0


def test_negative_similarity_clamps_to_0(make_profile: MakeProfile) -> None:
    profile = make_profile()
    client = FakeEmbeddingsClient(
        vectors={build_profile_text(profile): (-1.0, 0.0)},
        default=(1.0, 0.0),
    )

    result = run_score(make_layer(client), profile)

    assert result.score == 0
    assert result.similarity == pytest.approx(-1.0)


def test_job_and_profile_texts_are_bounded_before_embedding(
    make_profile: MakeProfile,
) -> None:
    client = FakeEmbeddingsClient()
    layer = make_layer(client)
    huge_description = "x" * 10_000

    asyncio.run(
        layer.score(
            ScoreJobInput(title="Dev", description=huge_description),
            make_profile(),
        )
    )

    embedded_texts = [text for call in client.calls for text in call]
    assert all(len(text) <= MAX_JOB_CHARS for text in embedded_texts)
    assert len(build_profile_text(make_profile())) <= MAX_PROFILE_CHARS


def test_profile_embedding_is_cached_across_jobs(make_profile: MakeProfile) -> None:
    profile = make_profile()
    client = FakeEmbeddingsClient()
    layer = make_layer(client)

    asyncio.run(layer.score(ScoreJobInput(title="Job A", description=None), profile))
    asyncio.run(layer.score(ScoreJobInput(title="Job B", description=None), profile))

    embedded_texts = [text for call in client.calls for text in call]
    profile_text = build_profile_text(profile)
    assert embedded_texts.count(profile_text) == 1


def test_provider_failure_degrades_to_layer_unavailable(
    make_profile: MakeProfile,
) -> None:
    client = FakeEmbeddingsClient(
        error=AIConnectError("down", provider="ollama", model="fake-embed")
    )

    with pytest.raises(ScoreLayerUnavailableError):
        run_score(make_layer(client), make_profile())


def test_pipeline_still_scores_when_semantic_layer_is_down(
    make_profile: MakeProfile,
) -> None:
    client = FakeEmbeddingsClient(
        error=AIConnectError("down", provider="ollama", model="fake-embed")
    )
    registry = ScoreLayerRegistry()
    registry.register(KeywordScoreLayer(), weight=1.0, required=True)
    registry.register(make_layer(client), weight=0.5)

    result = asyncio.run(
        score_job(
            ScoreJobInput(
                title="Python Developer", description=None, location="Remote"
            ),
            make_profile(),
            registry=registry,
        )
    )

    assert result.status == "scored"
    assert result.score == 100  # keyword layer alone, renormalized
    assert result.layer_outcomes[1].status == "skip"


def test_job_without_text_skips_the_semantic_layer(
    make_profile: MakeProfile,
) -> None:
    layer = make_layer(FakeEmbeddingsClient())

    with pytest.raises(ScoreLayerUnavailableError):
        asyncio.run(
            layer.score(ScoreJobInput(title=None, description=None), make_profile())
        )


def test_mismatched_embedding_dimensions_skip_instead_of_crashing(
    make_profile: MakeProfile,
) -> None:
    profile = make_profile()
    client = FakeEmbeddingsClient(
        vectors={build_profile_text(profile): (1.0, 0.0, 0.0)},
        default=(1.0, 0.0),
    )

    with pytest.raises(ScoreLayerUnavailableError):
        run_score(make_layer(client), profile)


def test_non_finite_vector_values_score_0_not_100(
    make_profile: MakeProfile,
) -> None:
    profile = make_profile()
    infinity = float("inf")
    client = FakeEmbeddingsClient(
        vectors={build_profile_text(profile): (infinity, 1.0)},
        default=(infinity, 2.0),
    )

    result = run_score(make_layer(client), profile)

    assert result.score == 0


def test_result_type_lives_in_the_shared_models_module(
    make_profile: MakeProfile,
) -> None:
    result = run_score(make_layer(FakeEmbeddingsClient()), make_profile())

    assert isinstance(result, SemanticScoreResult)


def test_identical_inputs_produce_identical_results(make_profile: MakeProfile) -> None:
    profile = make_profile()

    first = run_score(make_layer(FakeEmbeddingsClient()), profile)
    second = run_score(make_layer(FakeEmbeddingsClient()), profile)

    assert first == second


def test_cosine_and_normalization_helpers_are_deterministic() -> None:
    assert cosine_similarity((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_similarity((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)
    assert cosine_similarity((0.0, 0.0), (1.0, 0.0)) == 0.0
    assert similarity_to_score(0.5) == 50
    assert similarity_to_score(1.5) == 100
    assert similarity_to_score(-0.3) == 0


def test_job_text_joins_title_and_description() -> None:
    assert build_job_text("Dev", "Build APIs.") == "Dev Build APIs."
    assert build_job_text(None, "Build APIs.") == "Build APIs."
    assert build_job_text("Dev", None) == "Dev"
    assert build_job_text(None, None) == ""
    assert build_job_text("  ", "\n\t") == ""


def test_ollama_embeddings_client_uses_mocked_transport() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = request.read().decode()
        return httpx.Response(200, json={"embeddings": [[1, 0.5]]})

    client = OllamaEmbeddingsClient(
        "http://localhost:11434/",
        transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(client.embed(["Python engineer"]))

    assert result == [(1.0, 0.5)]
    assert captured["url"] == "http://localhost:11434/api/embed"
    assert "Python engineer" in str(captured["body"])


@pytest.mark.parametrize(
    "vector",
    [[True, 1], ["1", 2], [float("inf"), 1]],
)
def test_malformed_embedding_values_map_to_typed_error(vector: list[object]) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=json.dumps({"embeddings": [vector]}),
            headers={"content-type": "application/json"},
        )
    )
    client = OllamaEmbeddingsClient("http://localhost", transport=transport)

    with pytest.raises(AIResponseError):
        asyncio.run(client.embed(["text"]))


def test_inconsistent_batch_dimensions_map_to_typed_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"embeddings": [[1, 0], [1, 0, 0]]},
        )
    )
    client = OllamaEmbeddingsClient("http://localhost", transport=transport)

    with pytest.raises(AIResponseError, match="dimensions"):
        asyncio.run(client.embed(["one", "two"]))


def test_embedding_redirect_maps_to_typed_http_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(307, headers={"location": "/other"})
    )
    client = OllamaEmbeddingsClient("http://localhost", transport=transport)

    with pytest.raises(AIHTTPError) as excinfo:
        asyncio.run(client.embed(["text"]))

    assert excinfo.value.status_code == 307


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"base_url": " "}, "base URL"),
        ({"base_url": "http://localhost", "model": " "}, "model"),
        ({"base_url": "http://localhost", "timeout_seconds": 0}, "timeout"),
    ],
)
def test_invalid_embedding_client_settings_are_rejected(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        OllamaEmbeddingsClient(**kwargs)  # type: ignore[arg-type]
