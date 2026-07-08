"""Tests for the completion protocol's Ollama adapter.

Every case injects an httpx.MockTransport, so the suite exercises payload
construction, response normalization, and typed-error mapping without a
live Ollama server or any network access.
"""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.models.config import OllamaConfig
from app.services.ai.completion import CompletionRequest
from app.services.ai.errors import (
    AIConnectError,
    AIHTTPError,
    AIResponseError,
    AITimeoutError,
)
from app.services.ai.ollama import OllamaCompletionProvider


def make_ollama_config() -> OllamaConfig:
    """Build the validated config shape the adapter consumes."""
    return OllamaConfig.model_validate(
        {
            "base_url": "http://localhost:11434",
            "scorer": {"model": "qwen2.5:7b", "temperature": 0.1, "max_tokens": 512},
            "tailor": {"model": "qwen2.5:14b", "temperature": 0.3, "max_tokens": 2048},
        }
    )


def ollama_body(**overrides: Any) -> dict[str, Any]:
    """A realistic successful /api/chat response body."""
    body: dict[str, Any] = {
        "model": "qwen2.5:7b",
        "message": {"role": "assistant", "content": "hello"},
        "done_reason": "stop",
        "prompt_eval_count": 12,
        "eval_count": 34,
        "total_duration": 5_000_000,
    }
    body.update(overrides)
    return body


def make_provider(
    handler: httpx.MockTransport,
    role: str = "scorer",
) -> OllamaCompletionProvider:
    return OllamaCompletionProvider(
        make_ollama_config(),
        role,  # type: ignore[arg-type]
        transport=handler,
    )


def test_success_maps_text_identity_finish_and_usage() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=ollama_body())
    )

    response = asyncio.run(
        make_provider(transport).complete(CompletionRequest(prompt="hi"))
    )

    assert response.text == "hello"
    assert response.provider == "ollama"
    assert response.model == "qwen2.5:7b"
    assert response.finish_reason == "stop"
    assert response.raw_usage["eval_count"] == 34
    assert response.duration_ms >= 0


def test_request_payload_uses_scorer_role_config() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=ollama_body())

    asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(prompt="score this", system_prompt="be strict")
        )
    )

    assert captured["model"] == "qwen2.5:7b"
    assert captured["stream"] is False
    assert captured["options"] == {"temperature": 0.1, "num_predict": 512}
    assert captured["messages"][0] == {"role": "system", "content": "be strict"}
    assert captured["messages"][1] == {"role": "user", "content": "score this"}


def test_request_overrides_replace_role_defaults() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=ollama_body())

    asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(prompt="hi", temperature=0.9, max_tokens=64)
        )
    )

    assert captured["options"] == {"temperature": 0.9, "num_predict": 64}


def test_response_schema_passes_through_as_format() -> None:
    captured: dict[str, Any] = {}
    schema = {"type": "object", "properties": {"score": {"type": "integer"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=ollama_body())

    asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(prompt="hi", response_schema=schema)
        )
    )

    assert captured["format"] == schema


def test_tailor_role_selects_tailor_model() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=ollama_body(model="qwen2.5:14b"))

    provider = make_provider(httpx.MockTransport(handler), role="tailor")
    asyncio.run(provider.complete(CompletionRequest(prompt="tailor this")))

    assert provider.model == "qwen2.5:14b"
    assert captured["model"] == "qwen2.5:14b"
    assert captured["options"] == {"temperature": 0.3, "num_predict": 2048}


def test_connect_failure_maps_to_typed_error_with_identity() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(AIConnectError) as excinfo:
        asyncio.run(
            make_provider(httpx.MockTransport(handler)).complete(
                CompletionRequest(prompt="hi")
            )
        )

    assert excinfo.value.provider == "ollama"
    assert excinfo.value.model == "qwen2.5:7b"


def test_timeout_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    with pytest.raises(AITimeoutError):
        asyncio.run(
            make_provider(httpx.MockTransport(handler)).complete(
                CompletionRequest(prompt="hi")
            )
        )


def test_http_error_status_maps_to_typed_error_with_status() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(500, text="internal error")
    )

    with pytest.raises(AIHTTPError) as excinfo:
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))

    assert excinfo.value.status_code == 500


def test_non_json_body_maps_to_response_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not json at all")
    )

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


def test_missing_message_content_maps_to_response_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"model": "qwen2.5:7b", "done": True})
    )

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


def test_unrecognized_done_reason_degrades_to_unknown() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=ollama_body(done_reason="mystery"))
    )

    response = asyncio.run(
        make_provider(transport).complete(CompletionRequest(prompt="hi"))
    )

    assert response.finish_reason == "unknown"
