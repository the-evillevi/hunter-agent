"""Ollama adapter for the provider-neutral completion protocol.

This module exists so the local Ollama server is just one implementation
behind the completion boundary. It maps validated config (base URL plus a
scorer or tailor role) onto Ollama's /api/chat endpoint and translates
transport failures into the shared typed errors. Tests inject an httpx
MockTransport, so nothing here ever needs a live server.
"""

import json
import time
from typing import Any, Literal

import httpx

from app.models.config import OllamaConfig
from app.services.ai.completion import CompletionRequest, CompletionResponse
from app.services.ai.errors import (
    AIConnectError,
    AIHTTPError,
    AIResponseError,
    AITimeoutError,
)


PROVIDER_NAME = "ollama"

# Safe client default until timeouts earn a place in typed config: local
# models on the documented 16GB machine can take a while on first load.
DEFAULT_TIMEOUT_SECONDS = 120.0

# Ollama reports why generation stopped in done_reason; anything it may
# add later degrades to "unknown" instead of failing the response.
FINISH_REASONS: dict[str, Literal["stop", "length"]] = {
    "stop": "stop",
    "length": "length",
}


class OllamaCompletionProvider:
    """Completion provider backed by a local Ollama server.

    One instance serves one configured role (scorer or tailor); both roles
    can be instantiated against the same server and used sequentially,
    which is how the single local machine runs the whole pipeline.
    """

    provider_name = PROVIDER_NAME

    def __init__(
        self,
        config: OllamaConfig,
        role: Literal["scorer", "tailor"],
        *,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        role_config = config.scorer if role == "scorer" else config.tailor
        self.model = role_config.model
        self._base_url = str(config.base_url).rstrip("/")
        self._temperature = role_config.temperature
        self._max_tokens = role_config.max_tokens
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        """Send one chat completion to Ollama and normalize the response."""
        payload = self._build_payload(request)
        started = time.perf_counter()

        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=self._timeout_seconds,
            ) as client:
                response = await client.post(
                    f"{self._base_url}/api/chat",
                    json=payload,
                )
        except httpx.TimeoutException as error:
            raise AITimeoutError(
                f"Ollama timed out after {self._timeout_seconds}s: {error}",
                provider=self.provider_name,
                model=self.model,
            ) from error
        except httpx.TransportError as error:
            raise AIConnectError(
                f"could not reach Ollama at {self._base_url}: {error}",
                provider=self.provider_name,
                model=self.model,
            ) from error

        duration_ms = max(0, round((time.perf_counter() - started) * 1000))

        if response.status_code >= 400:
            raise AIHTTPError(
                f"Ollama returned HTTP {response.status_code}",
                provider=self.provider_name,
                model=self.model,
                status_code=response.status_code,
            )

        return self._parse_response(response, duration_ms)

    def _build_payload(self, request: CompletionRequest) -> dict[str, Any]:
        """Translate the neutral request into Ollama's chat payload."""
        messages: list[dict[str, str]] = []
        if request.system_prompt:
            messages.append({"role": "system", "content": request.system_prompt})
        messages.append({"role": "user", "content": request.prompt})

        temperature = (
            request.temperature
            if request.temperature is not None
            else self._temperature
        )
        max_tokens = (
            request.max_tokens if request.max_tokens is not None else self._max_tokens
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        # Ollama's native structured outputs accept a JSON schema directly,
        # which is exactly the protocol's provider-neutral contract.
        if request.response_schema is not None:
            payload["format"] = request.response_schema
        return payload

    def _parse_response(
        self,
        response: httpx.Response,
        duration_ms: int,
    ) -> CompletionResponse:
        """Validate the Ollama body and lift it into the neutral shape."""
        try:
            body = response.json()
        except (json.JSONDecodeError, ValueError) as error:
            raise AIResponseError(
                f"Ollama returned a non-JSON body: {error}",
                provider=self.provider_name,
                model=self.model,
            ) from error

        message = body.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise AIResponseError(
                "Ollama response is missing message.content",
                provider=self.provider_name,
                model=self.model,
            )

        raw_usage = {
            key: body[key]
            for key in (
                "prompt_eval_count",
                "eval_count",
                "total_duration",
                "load_duration",
            )
            if key in body
        }

        return CompletionResponse(
            text=message["content"],
            provider=self.provider_name,
            model=str(body.get("model", self.model)),
            duration_ms=duration_ms,
            finish_reason=FINISH_REASONS.get(str(body.get("done_reason")), "unknown"),
            raw_usage=raw_usage,
        )
