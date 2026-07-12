"""Data-driven construction of configured cloud completion providers."""

from collections.abc import Mapping
from typing import Literal

import httpx

from app.models.config import CloudAIConfig, CloudModelConfig
from app.services.ai.anthropic import AnthropicCompletionProvider
from app.services.ai.completion import CompletionProvider
from app.services.ai.openai import OpenAICompletionProvider


CloudRole = Literal["generator", "critic"]

# Dispatch is data-driven so role assignment stays symmetric: any provider
# can fill either role, including the same provider in both.
_PROVIDERS = {
    "anthropic": AnthropicCompletionProvider,
    "openai": OpenAICompletionProvider,
}


def create_cloud_completion_provider(
    config: CloudAIConfig,
    role: CloudRole,
    *,
    override: CloudModelConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    environment: Mapping[str, str] | None = None,
) -> CompletionProvider:
    """Create the configured provider for a role, with per-run override."""
    role_config = override or getattr(config, role)
    provider_type = _PROVIDERS[role_config.provider]
    return provider_type(
        role_config,
        transport=transport,
        environment=environment,
    )
