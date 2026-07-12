"""Anthropic completion adapter tests with mocked HTTP transports only."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.models.config import CloudAIConfig, CloudModelConfig
from app.services.ai.anthropic import AnthropicCompletionProvider
from app.services.ai.completion import CompletionRequest
from app.services.ai.errors import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectError,
    AIHTTPError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
)
from app.services.ai.factory import create_cloud_completion_provider
from app.services.ai.openai import OpenAICompletionProvider


def cloud_model(
    model: str = "claude-opus-4-8",
    *,
    provider: str = "anthropic",
    temperature: float = 0.2,
    max_tokens: int = 1000,
) -> CloudModelConfig:
    return CloudModelConfig(
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def make_provider(transport: httpx.MockTransport) -> AnthropicCompletionProvider:
    return AnthropicCompletionProvider(
        cloud_model(),
        transport=transport,
        environment={"ANTHROPIC_API_KEY": "anthropic-test-key"},
    )


def response_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "claude-opus-4-8-20260115",
        "content": [{"type": "text", "text": "anthropic answer"}],
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    body.update(overrides)
    return body


def test_success_uses_messages_api_and_retains_audit_data() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=response_body())

    response = asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(prompt="draft", system_prompt="be helpful")
        )
    )

    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "anthropic-test-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"] == {
        "model": "claude-opus-4-8",
        "messages": [{"role": "user", "content": "draft"}],
        "max_tokens": 1000,
        "temperature": 0.2,
        "system": "be helpful",
    }
    assert response.text == "anthropic answer"
    assert response.provider == "anthropic"
    assert response.model == "claude-opus-4-8-20260115"
    assert response.finish_reason == "stop"
    assert response.raw_usage["output_tokens"] == 7
    assert response.duration_ms >= 0


def test_structured_output_and_request_overrides_are_native() -> None:
    captured: dict[str, Any] = {}
    schema = {
        "type": "object",
        "properties": {"fit": {"type": "string"}},
        "required": ["fit"],
        "additionalProperties": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json=response_body(
                content=[{"type": "text", "text": '{"fit":"good"}'}],
                stop_reason="max_tokens",
            ),
        )

    response = asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(
                prompt="draft",
                response_schema=schema,
                temperature=0.0,
                max_tokens=99,
            )
        )
    )

    assert captured["temperature"] == 0.0
    assert captured["max_tokens"] == 99
    assert captured["output_config"] == {
        "format": {"type": "json_schema", "schema": schema}
    }
    assert response.text == '{"fit":"good"}'
    assert response.finish_reason == "length"


def test_missing_api_key_is_a_clear_startup_configuration_error() -> None:
    with pytest.raises(AIConfigurationError, match="ANTHROPIC_API_KEY"):
        AnthropicCompletionProvider(cloud_model(), environment={})


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (401, AIAuthenticationError),
        (403, AIAuthenticationError),
        (429, AIRateLimitError),
        (500, AIHTTPError),
    ],
)
def test_http_failures_map_to_typed_errors(
    status: int,
    error_type: type[AIHTTPError],
) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, text="provider detail must stay private")
    )

    with pytest.raises(error_type) as excinfo:
        asyncio.run(
            make_provider(transport).complete(CompletionRequest(prompt="hello"))
        )

    assert excinfo.value.status_code == status
    assert "provider detail" not in str(excinfo.value)


@pytest.mark.parametrize(
    ("transport_error", "error_type"),
    [
        (httpx.ConnectError("offline"), AIConnectError),
        (httpx.ReadTimeout("slow"), AITimeoutError),
    ],
)
def test_transport_failures_map_to_typed_errors(
    transport_error: httpx.TransportError,
    error_type: type[Exception],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise transport_error

    with pytest.raises(error_type):
        asyncio.run(
            make_provider(httpx.MockTransport(handler)).complete(
                CompletionRequest(prompt="hello")
            )
        )


@pytest.mark.parametrize(
    "body",
    [
        response_body(content=[]),
        response_body(content=[{"type": "text", "text": "   "}]),
        response_body(usage=[]),
        response_body(model=""),
    ],
)
def test_malformed_success_responses_map_to_response_error(
    body: dict[str, Any],
) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))

    with pytest.raises(AIResponseError):
        asyncio.run(
            make_provider(transport).complete(CompletionRequest(prompt="hello"))
        )


def test_non_json_success_maps_to_response_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text="not-json")
    )
    with pytest.raises(AIResponseError):
        asyncio.run(
            make_provider(transport).complete(CompletionRequest(prompt="hello"))
        )


def test_unknown_stop_reason_is_reported_as_unknown_finish() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body(stop_reason="tool_use"))
    )

    response = asyncio.run(
        make_provider(transport).complete(CompletionRequest(prompt="hello"))
    )

    assert response.finish_reason == "unknown"


def test_factory_dispatches_by_provider_and_supports_mixed_pairings() -> None:
    """Role assignment is data: the decided default pairing and its swap."""
    environment = {
        "ANTHROPIC_API_KEY": "anthropic-test-key",
        "OPENAI_API_KEY": "openai-test-key",
    }
    config = CloudAIConfig(
        generator=cloud_model(),
        critic=cloud_model("gpt-5.5", provider="openai"),
    )

    generator = create_cloud_completion_provider(
        config, "generator", environment=environment
    )
    critic = create_cloud_completion_provider(config, "critic", environment=environment)
    swapped = create_cloud_completion_provider(
        config,
        "generator",
        override=cloud_model("gpt-5.5", provider="openai"),
        environment=environment,
    )
    doubled = create_cloud_completion_provider(
        config,
        "critic",
        override=cloud_model(),
        environment=environment,
    )

    assert isinstance(generator, AnthropicCompletionProvider)
    assert isinstance(critic, OpenAICompletionProvider)
    assert isinstance(swapped, OpenAICompletionProvider)
    assert isinstance(doubled, AnthropicCompletionProvider)


def test_wrong_provider_config_is_rejected_before_network_use() -> None:
    invalid = cloud_model(provider="openai", model="gpt-5.5")

    with pytest.raises(ValueError, match="provider='anthropic'"):
        AnthropicCompletionProvider(
            invalid,
            environment={"ANTHROPIC_API_KEY": "test"},
        )


def test_temperature_is_omitted_unless_configured_or_requested() -> None:
    """claude-opus-4-8 rejects sampling parameters; None must mean absent."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json=response_body())

    provider = AnthropicCompletionProvider(
        CloudModelConfig(provider="anthropic", model="claude-opus-4-8", max_tokens=64),
        transport=httpx.MockTransport(handler),
        environment={"ANTHROPIC_API_KEY": "anthropic-test-key"},
    )
    asyncio.run(provider.complete(CompletionRequest(prompt="draft")))

    assert "temperature" not in captured


def test_temperature_above_anthropic_cap_is_a_configuration_error() -> None:
    """The protocol allows up to 2; Anthropic caps at 1 — fail clearly."""
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=response_body())
    )

    with pytest.raises(AIConfigurationError, match="<= 1"):
        asyncio.run(
            make_provider(transport).complete(
                CompletionRequest(prompt="draft", temperature=1.5)
            )
        )


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        AnthropicCompletionProvider(
            cloud_model(),
            environment={"ANTHROPIC_API_KEY": "test"},
            timeout_seconds=timeout,
        )
