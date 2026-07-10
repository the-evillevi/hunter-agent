"""Shared HTTP behavior for cloud completion adapters."""

import math
import os
from collections.abc import Mapping

import httpx

from app.services.ai.errors import (
    AIAuthenticationError,
    AIConfigurationError,
    AIHTTPError,
    AIRateLimitError,
)


DEFAULT_CLOUD_TIMEOUT_SECONDS = 120.0


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
