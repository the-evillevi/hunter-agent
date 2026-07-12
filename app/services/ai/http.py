"""Shared HTTP plumbing for AI provider clients.

Local and cloud clients translate the same httpx failures into the
package's typed errors. Centralizing the POST-and-map step (``post_json``,
used by the Ollama completion and embeddings clients) and the
cloud-provider helpers (API-key lookup, timeout validation, status
mapping) keeps the adapters from drifting apart as error handling
evolves.
"""

import math
import os
from collections.abc import Mapping
from typing import Any

import httpx

from app.services.ai.errors import (
    AIAuthenticationError,
    AIConfigurationError,
    AIConnectError,
    AIHTTPError,
    AIRateLimitError,
    AIResponseError,
    AITimeoutError,
)


DEFAULT_CLOUD_TIMEOUT_SECONDS = 120.0


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
    headers: Mapping[str, str] | None = None,
) -> httpx.Response:
    """POST one JSON payload and map transport failures to typed errors.

    Callers still own response-body validation; this helper only guarantees
    that connection, timeout, and HTTP-status failures never leak httpx
    exceptions upward. Auth and rate-limit statuses raise their dedicated
    AIHTTPError subclasses.
    """
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
        ) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as error:
        raise AITimeoutError(
            f"{provider} timed out after {timeout_seconds}s: {error}",
            provider=provider,
            model=model,
        ) from error
    except httpx.TransportError as error:
        raise AIConnectError(
            f"could not reach {provider} at {url}: {error}",
            provider=provider,
            model=model,
        ) from error
    raise_for_provider_status(response, provider=provider, model=model)
    return response


def json_object_body(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    """Parse a success response as a JSON object or raise a typed error."""
    try:
        body = response.json()
    except ValueError as error:
        raise AIResponseError(
            f"{provider} returned non-JSON: {error}",
            provider=provider,
            model=model,
        ) from error
    if not isinstance(body, dict):
        raise AIResponseError(
            f"{provider} response JSON is not an object",
            provider=provider,
            model=model,
        )
    return body


def validate_timeout(timeout_seconds: float, provider: str) -> None:
    """Reject timeout values that httpx cannot safely enforce."""
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError(f"{provider} timeout must be finite and positive")


def require_api_key(
    variable_name: str,
    *,
    provider: str,
    model: str,
    environment: Mapping[str, str] | None,
) -> str:
    """Read a non-blank API key from the process environment."""
    source = os.environ if environment is None else environment
    api_key = source.get(variable_name, "").strip()
    if not api_key:
        raise AIConfigurationError(
            f"{variable_name} must be set before starting the {provider} provider",
            provider=provider,
            model=model,
        )
    return api_key


def raise_for_provider_status(
    response: httpx.Response,
    *,
    provider: str,
    model: str,
) -> None:
    """Map provider HTTP statuses without exposing response bodies or secrets."""
    if response.is_success:
        return

    error_type: type[AIHTTPError]
    if response.status_code in (401, 403):
        error_type = AIAuthenticationError
    elif response.status_code == 429:
        error_type = AIRateLimitError
    else:
        error_type = AIHTTPError

    raise error_type(
        f"{provider} returned HTTP {response.status_code}",
        provider=provider,
        model=model,
        status_code=response.status_code,
    )
