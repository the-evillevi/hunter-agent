"""Ollama embeddings client for the semantic scoring layer.

This module exists because the completion protocol (completion.py) covers
generative calls only; embeddings have their own small client rather than
a premature shared protocol, since Ollama is the only implementation
(2026-07-07 decision: reuse the local Ollama server, add no Python
embedding dependencies). It shares the typed error hierarchy and the
injectable-transport testing pattern with the completion adapter.
"""

import json
import math

import httpx

from app.services.ai.errors import AIResponseError
from app.services.ai.http import post_json


PROVIDER_NAME = "ollama"

# The model is a constructor default, not typed config: OllamaConfig has
# no embeddings role yet, and adding one is out of scope for this story.
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"

# Embeddings are much faster than generation; a shorter default timeout
# still leaves room for first-load of the model on the 16GB machine.
DEFAULT_TIMEOUT_SECONDS = 30.0


class OllamaEmbeddingsClient:
    """Embeds batches of texts through a local Ollama server."""

    provider_name = PROVIDER_NAME

    def __init__(
        self,
        base_url: str,
        model: str = DEFAULT_EMBEDDING_MODEL,
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.model = model.strip()
        self._base_url = base_url.strip().rstrip("/")
        if not self.model:
            raise ValueError("embedding model must not be blank")
        if not self._base_url:
            raise ValueError("embedding base URL must not be blank")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("embedding timeout must be finite and positive")
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def embed(self, texts: list[str]) -> list[tuple[float, ...]]:
        """Return one embedding vector per input text, in input order."""
        response = await post_json(
            f"{self._base_url}/api/embed",
            {"model": self.model, "input": texts},
            provider=self.provider_name,
            model=self.model,
            timeout_seconds=self._timeout_seconds,
            transport=self._transport,
        )
        return self._parse_response(response, expected_count=len(texts))

    def _parse_response(
        self,
        response: httpx.Response,
        *,
        expected_count: int,
    ) -> list[tuple[float, ...]]:
        """Validate the /api/embed body shape before anyone does math on it."""
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise AIResponseError(
                f"Ollama embeddings returned a non-JSON body: {error}",
                provider=self.provider_name,
                model=self.model,
            ) from error

        embeddings = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != expected_count:
            raise AIResponseError(
                "Ollama embeddings response is missing one vector per input",
                provider=self.provider_name,
                model=self.model,
            )

        vectors: list[tuple[float, ...]] = []
        for vector in embeddings:
            if not isinstance(vector, list) or not vector:
                raise AIResponseError(
                    "Ollama embeddings returned an empty or malformed vector",
                    provider=self.provider_name,
                    model=self.model,
                )
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in vector
            ):
                raise AIResponseError(
                    "Ollama embeddings returned non-finite or non-numeric values",
                    provider=self.provider_name,
                    model=self.model,
                )
            vectors.append(tuple(float(value) for value in vector))

        if vectors and any(len(vector) != len(vectors[0]) for vector in vectors[1:]):
            raise AIResponseError(
                "Ollama embeddings returned inconsistent vector dimensions",
                provider=self.provider_name,
                model=self.model,
            )
        return vectors
