"""Shared HTTP plumbing for AI provider clients.

The completion adapter and the embeddings client talk to the same local
Ollama server and must translate the same httpx failures into the
package's typed errors. Centralizing the POST-and-map step keeps the two
clients from drifting apart as error handling evolves.
"""

from typing import Any

import httpx

from app.services.ai.errors import AIConnectError, AIHTTPError, AITimeoutError


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    provider: str,
    model: str,
    timeout_seconds: float,
    transport: httpx.AsyncBaseTransport | None,
) -> httpx.Response:
    """POST one JSON payload and map transport failures to typed errors.

    Callers still own response-body validation; this helper only guarantees
    that connection, timeout, and HTTP-status failures never leak httpx
    exceptions upward.
    """
    try:
        async with httpx.AsyncClient(
            transport=transport,
            timeout=timeout_seconds,
        ) as client:
            response = await client.post(url, json=payload)
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

    if not response.is_success:
        raise AIHTTPError(
            f"{provider} returned HTTP {response.status_code}",
            provider=provider,
            model=model,
            status_code=response.status_code,
        )
    return response
