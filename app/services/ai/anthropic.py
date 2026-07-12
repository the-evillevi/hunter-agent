"""Anthropic Messages API adapter for the neutral completion protocol."""

import time
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from app.models.config import CloudModelConfig
from app.services.ai.completion import CompletionRequest, CompletionResponse
from app.services.ai.errors import AIConfigurationError, AIResponseError
from app.services.ai.http import (
    DEFAULT_CLOUD_TIMEOUT_SECONDS,
    json_object_body,
    post_json,
    require_api_key,
    validate_timeout,
)


PROVIDER_NAME = "anthropic"
API_KEY_VARIABLE = "ANTHROPIC_API_KEY"
DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"

# The completion protocol allows temperature up to 2 (OpenAI's range);
# Anthropic caps it at 1, so the adapter rejects higher values with a
# clear error instead of letting the API answer 400.
MAX_TEMPERATURE = 1.0


class AnthropicCompletionProvider:
    """Complete prompts through Anthropic without leaking its API into features."""

    provider_name = PROVIDER_NAME

    def __init__(
        self,
        config: CloudModelConfig,
        *,
        timeout_seconds: float = DEFAULT_CLOUD_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        environment: Mapping[str, str] | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        if config.provider != self.provider_name:
            raise ValueError("Anthropic provider requires provider='anthropic'")
        validate_timeout(timeout_seconds, self.provider_name)
        self.model = config.model
        self._temperature = config.temperature
        self._max_tokens = config.max_tokens
        self._timeout_seconds = timeout_seconds
        self._transport = transport
        self._base_url = base_url.rstrip("/")
        self._api_key = require_api_key(
            API_KEY_VARIABLE,
            provider=self.provider_name,
            model=self.model,
            environment=environment,
        )

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send one Messages request and normalize its response."""
        started = time.perf_counter()
        response = await post_json(
            f"{self._base_url}/v1/messages",
            self._build_payload(request),
            provider=self.provider_name,
            model=self.model,
            timeout_seconds=self._timeout_seconds,
            transport=self._transport,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": API_VERSION,
            },
        )
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return self._parse_response(response, duration_ms)

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_tokens or self._max_tokens,
        }
        # Opus 4.7/4.8-generation models (the default) reject sampling
        # parameters, so temperature is only sent when someone asked for it.
        temperature = (
            request.temperature
            if request.temperature is not None
            else self._temperature
        )
        if temperature is not None:
            if temperature > MAX_TEMPERATURE:
                raise AIConfigurationError(
                    f"Anthropic temperature must be <= {MAX_TEMPERATURE}, "
                    f"got {temperature}",
                    provider=self.provider_name,
                    model=self.model,
                )
            payload["temperature"] = temperature
        if request.system_prompt is not None:
            payload["system"] = request.system_prompt
        if request.response_schema is not None:
            # Anthropic's native structured outputs: the model must emit
            # JSON matching this schema.
            payload["output_config"] = {
                "format": {
                    "type": "json_schema",
                    "schema": request.response_schema,
                }
            }
        return payload

    def _parse_response(
        self,
        response: httpx.Response,
        duration_ms: int,
    ) -> CompletionResponse:
        body = json_object_body(response, provider=self.provider_name, model=self.model)

        text = _collect_content_text(body.get("content"))
        if not text.strip():
            raise _response_error(
                "Anthropic response has no non-blank text content", self.model
            )

        response_model = body.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            raise _response_error(
                "Anthropic response contains an invalid model identity", self.model
            )
        usage = body.get("usage", {})
        if not isinstance(usage, dict):
            raise _response_error(
                "Anthropic response usage is not an object", self.model
            )

        return CompletionResponse(
            text=text,
            provider=self.provider_name,
            model=response_model,
            duration_ms=duration_ms,
            finish_reason=_finish_reason(body.get("stop_reason")),
            raw_usage=usage,
        )


def _collect_content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    text_parts: list[str] = []
    for block in content:
        if (
            isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ):
            text_parts.append(block["text"])
    return "".join(text_parts)


def _finish_reason(stop_reason: Any) -> Literal["stop", "length", "unknown"]:
    if stop_reason in ("end_turn", "stop_sequence"):
        return "stop"
    if stop_reason == "max_tokens":
        return "length"
    return "unknown"


def _response_error(message: str, model: str) -> AIResponseError:
    return AIResponseError(message, provider=PROVIDER_NAME, model=model)
