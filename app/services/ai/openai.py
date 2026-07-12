"""OpenAI Responses API adapter for the neutral completion protocol."""

import time
from collections.abc import Mapping
from typing import Any, Literal

import httpx

from app.models.config import CloudModelConfig
from app.services.ai.completion import CompletionRequest, CompletionResponse
from app.services.ai.errors import AIResponseError
from app.services.ai.http import (
    DEFAULT_CLOUD_TIMEOUT_SECONDS,
    json_object_body,
    post_json,
    require_api_key,
    validate_timeout,
)


PROVIDER_NAME = "openai"
API_KEY_VARIABLE = "OPENAI_API_KEY"
DEFAULT_BASE_URL = "https://api.openai.com"
STRUCTURED_OUTPUT_NAME = "completion_response"


class OpenAICompletionProvider:
    """Complete prompts through OpenAI without leaking its API into features."""

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
            raise ValueError("OpenAI provider requires provider='openai'")
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
        """Send one Responses request and normalize its response."""
        started = time.perf_counter()
        response = await post_json(
            f"{self._base_url}/v1/responses",
            self._build_payload(request),
            provider=self.provider_name,
            model=self.model,
            timeout_seconds=self._timeout_seconds,
            transport=self._transport,
            headers={"authorization": f"Bearer {self._api_key}"},
        )
        duration_ms = max(0, round((time.perf_counter() - started) * 1000))
        return self._parse_response(response, duration_ms)

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": request.prompt,
            "max_output_tokens": request.max_tokens or self._max_tokens,
        }
        # Reasoning-family models (the gpt-5.5 default) reject sampling
        # parameters, so temperature is only sent when someone asked for it.
        temperature = (
            request.temperature
            if request.temperature is not None
            else self._temperature
        )
        if temperature is not None:
            payload["temperature"] = temperature
        if request.system_prompt is not None:
            payload["instructions"] = request.system_prompt
        if request.response_schema is not None:
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": STRUCTURED_OUTPUT_NAME,
                    "schema": request.response_schema,
                    "strict": True,
                }
            }
        return payload

    def _parse_response(
        self,
        response: httpx.Response,
        duration_ms: int,
    ) -> CompletionResponse:
        body = json_object_body(response, provider=self.provider_name, model=self.model)
        status = body.get("status")
        if status not in ("completed", "incomplete"):
            raise _response_error(
                f"OpenAI response has unusable status {status!r}", self.model
            )

        text = body.get("output_text")
        if not isinstance(text, str) or not text.strip():
            text = _collect_output_text(body.get("output"))
        if not text.strip():
            raise _response_error(
                "OpenAI response has no non-blank output text", self.model
            )

        response_model = body.get("model")
        if not isinstance(response_model, str) or not response_model.strip():
            raise _response_error(
                "OpenAI response contains an invalid model identity", self.model
            )
        usage = body.get("usage", {})
        if not isinstance(usage, dict):
            raise _response_error("OpenAI response usage is not an object", self.model)

        return CompletionResponse(
            text=text,
            provider=self.provider_name,
            model=response_model,
            duration_ms=duration_ms,
            finish_reason=_finish_reason(body),
            raw_usage=usage,
        )


def _collect_output_text(output: Any) -> str:
    if not isinstance(output, list):
        return ""
    text_parts: list[str] = []
    for item in output:
        if not isinstance(item, dict) or not isinstance(item.get("content"), list):
            continue
        for block in item["content"]:
            if (
                isinstance(block, dict)
                and block.get("type") == "output_text"
                and isinstance(block.get("text"), str)
            ):
                text_parts.append(block["text"])
    return "".join(text_parts)


def _finish_reason(body: dict[str, Any]) -> Literal["stop", "length", "unknown"]:
    if body.get("status") == "completed":
        return "stop"
    details = body.get("incomplete_details")
    if isinstance(details, dict) and details.get("reason") == "max_output_tokens":
        return "length"
    return "unknown"


def _response_error(message: str, model: str) -> AIResponseError:
    return AIResponseError(message, provider=PROVIDER_NAME, model=model)
