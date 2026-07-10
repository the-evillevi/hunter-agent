"""Data-driven construction of configured cloud completion providers."""

from collections.abc import Mapping
from typing import Literal

import httpx

from app.models.config import CloudAIConfig, CloudModelConfig
from app.services.ai.completion import CompletionProvider
from app.services.ai.openai import OpenAICompletionProvider


CloudRole = Literal["generator", "critic"]


def create_cloud_completion_provider(
    config: CloudAIConfig,
    role: CloudRole,
    *,
    override: CloudModelConfig | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    environment: Mapping[str, str] | None = None,
) -> CompletionProvider:
    """Create either OpenAI role, with an optional per-run model override."""
    role_config = override or getattr(config, role)
    return OpenAICompletionProvider(
        role_config,
        transport=transport,
        environment=environment,
    )
