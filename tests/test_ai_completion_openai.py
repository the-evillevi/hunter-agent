"""OpenAI completion adapter tests with mocked HTTP transports only."""

import asyncio
import json
from typing import Any

import httpx
import pytest

from app.models.config import CloudAIConfig, CloudModelConfig
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
    model: str = "gpt-5.5",
    *,
    temperature: float = 0.2,
    max_tokens: int = 1000,
) -> CloudModelConfig:
    return CloudModelConfig(
        provider="openai",
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def make_provider(transport: httpx.MockTransport) -> OpenAICompletionProvider:
    return OpenAICompletionProvider(
        cloud_model(),
        transport=transport,
        environment={"OPENAI_API_KEY": "openai-test-key"},
    )


def response_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": "gpt-5.5-2026-04-23",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "openai answer"}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 6, "total_tokens": 16},
    }
    body.update(overrides)
    return body


def test_success_uses_responses_api_and_retains_audit_data() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=response_body())

    response = asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(prompt="review", system_prompt="be strict")
        )
    )

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["headers"]["authorization"] == "Bearer openai-test-key"
    assert captured["payload"] == {
        "model": "gpt-5.5",
        "input": "review",
        "max_output_tokens": 1000,
        "temperature": 0.2,
        "instructions": "be strict",
    }
    assert response.text == "openai answer"
    assert response.provider == "openai"
    assert response.model == "gpt-5.5-2026-04-23"
    assert response.finish_reason == "stop"
    assert response.raw_usage["total_tokens"] == 16
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
                status="incomplete",
                incomplete_details={"reason": "max_output_tokens"},
                output_text='{"fit":"good"}',
            ),
        )

    response = asyncio.run(
        make_provider(httpx.MockTransport(handler)).complete(
            CompletionRequest(
                prompt="review",
                response_schema=schema,
                temperature=0.0,
                max_tokens=99,
            )
        )
    )

    assert captured["temperature"] == 0.0
    assert captured["max_output_tokens"] == 99
    assert captured["text"] == {
        "format": {
            "type": "json_schema",
            "name": "completion_response",
            "schema": schema,
            "strict": True,
        }
    }
    assert response.text == '{"fit":"good"}'
    assert response.finish_reason == "length"


def test_missing_api_key_is_a_clear_startup_configuration_error() -> None:
    with pytest.raises(AIConfigurationError, match="OPENAI_API_KEY"):
        OpenAICompletionProvider(cloud_model(), environment={})


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
        response_body(status="failed"),
        response_body(output=[]),
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


def test_factory_supports_same_provider_in_both_roles_and_per_run_model() -> None:
    config = CloudAIConfig(generator=cloud_model(), critic=cloud_model())
    environment = {"OPENAI_API_KEY": "openai-test-key"}

    generator = create_cloud_completion_provider(
        config, "generator", environment=environment
    )
    critic = create_cloud_completion_provider(config, "critic", environment=environment)
    overridden = create_cloud_completion_provider(
        config,
        "critic",
        override=cloud_model("gpt-5.5-2026-04-23"),
        environment=environment,
    )

    assert isinstance(generator, OpenAICompletionProvider)
    assert isinstance(critic, OpenAICompletionProvider)
    assert overridden.model == "gpt-5.5-2026-04-23"


@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        OpenAICompletionProvider(
            cloud_model(),
            environment={"OPENAI_API_KEY": "test"},
            timeout_seconds=timeout,
        )


def test_wrong_provider_config_is_rejected_before_network_use() -> None:
    invalid = cloud_model().model_copy(update={"provider": "anthropic"})

    with pytest.raises(ValueError, match="provider='openai'"):
        OpenAICompletionProvider(
            invalid,
            environment={"OPENAI_API_KEY": "test"},
        )
