"""Tests for the completion protocol's Ollama adapter.

Every case injects an httpx.MockTransport, so the suite exercises payload
construction, response normalization, and typed-error mapping without a
live Ollama server or any network access.
"""

import asyncio
import json
from typing import Any, Literal

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
    role: Literal["scorer", "tailor"] = "scorer",
) -> OllamaCompletionProvider:
    return OllamaCompletionProvider(
        make_ollama_config(),
        role,
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


def test_request_uses_configured_base_url_and_timeout() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["timeout"] = request.extensions["timeout"]
        return httpx.Response(200, json=ollama_body())

    provider = OllamaCompletionProvider(
        make_ollama_config(),
        "scorer",
        timeout_seconds=7.5,
        transport=httpx.MockTransport(handler),
    )
    asyncio.run(provider.complete(CompletionRequest(prompt="hi")))

    assert captured["url"] == "http://localhost:11434/api/chat"
    assert captured["timeout"] == {
        "connect": 7.5,
        "read": 7.5,
        "write": 7.5,
        "pool": 7.5,
    }


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


def test_scorer_and_tailor_roles_can_run_sequentially() -> None:
    requested_models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requested_models.append(payload["model"])
        return httpx.Response(200, json=ollama_body(model=payload["model"]))

    transport = httpx.MockTransport(handler)
    scorer = make_provider(transport, role="scorer")
    tailor = make_provider(transport, role="tailor")

    async def complete_in_sequence() -> None:
        await scorer.complete(CompletionRequest(prompt="score"))
        await tailor.complete(CompletionRequest(prompt="tailor"))

    asyncio.run(complete_in_sequence())

    assert requested_models == ["qwen2.5:7b", "qwen2.5:14b"]


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


def test_redirect_status_maps_to_typed_http_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(307, headers={"location": "/other"})
    )

    with pytest.raises(AIHTTPError) as excinfo:
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))

    assert excinfo.value.status_code == 307


def test_non_json_body_maps_to_response_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not json at all")
    )

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


def test_non_object_json_body_maps_to_response_error() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[1, 2, 3]))

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


def test_missing_message_content_maps_to_response_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"model": "qwen2.5:7b", "done": True})
    )

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


@pytest.mark.parametrize(
    "body",
    [
        ollama_body(message={"role": "assistant", "content": "   "}),
        ollama_body(model=""),
        ollama_body(model=42),
    ],
)
def test_blank_content_or_invalid_model_maps_to_response_error(
    body: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    with pytest.raises(AIResponseError):
        asyncio.run(make_provider(transport).complete(CompletionRequest(prompt="hi")))


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=ollama_body())
    )

    with pytest.raises(ValueError, match="finite and positive"):
        OllamaCompletionProvider(
            make_ollama_config(),
            "scorer",
            timeout_seconds=timeout,
            transport=transport,
        )


def test_invalid_runtime_role_is_rejected() -> None:
    with pytest.raises(ValueError, match="unsupported Ollama role"):
        OllamaCompletionProvider(make_ollama_config(), "invalid")  # type: ignore[arg-type]


def test_unrecognized_done_reason_degrades_to_unknown() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=ollama_body(done_reason="mystery"))
    )

    response = asyncio.run(
        make_provider(transport).complete(CompletionRequest(prompt="hi"))
    )

    assert response.finish_reason == "unknown"
