"""Typed errors shared by every AI provider adapter.

This module exists so callers can handle provider failures by kind
(connection, timeout, HTTP, malformed response) without knowing which
provider or HTTP library produced them. Adapters translate their native
exceptions into these types at the boundary.
"""


class AIProviderError(Exception):
    """Base error for any provider failure, tagged with provider identity."""

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class AIConnectError(AIProviderError):
    """The provider could not be reached at all (service down, bad URL)."""


class AITimeoutError(AIProviderError):
    """The provider did not answer within the configured timeout."""


class AIHTTPError(AIProviderError):
    """The provider answered with an HTTP error status."""

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        model: str,
        status_code: int,
    ) -> None:
        super().__init__(message, provider=provider, model=model)
        self.status_code = status_code


class AIResponseError(AIProviderError):
    """The provider answered, but the body was not the shape we require."""
