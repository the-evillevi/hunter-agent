"""Provider-neutral AI client boundary.

This package exists so feature code (LLM scoring, CV tailoring) depends on
one small completion contract instead of importing a provider directly.
The Ollama, Anthropic, and OpenAI adapters implement the same contract
and own their authentication details at the boundary.
"""

from app.services.ai.completion import (
    CompletionProvider,
    CompletionRequest,
    CompletionResponse,
)
from app.services.ai.anthropic import AnthropicCompletionProvider
from app.services.ai.factory import create_cloud_completion_provider
from app.services.ai.openai import OpenAICompletionProvider

__all__ = [
    "AnthropicCompletionProvider",
    "CompletionProvider",
    "CompletionRequest",
    "CompletionResponse",
    "OpenAICompletionProvider",
    "create_cloud_completion_provider",
]
