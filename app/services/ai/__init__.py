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
from app.services.ai.factory import create_cloud_completion_provider

# Only the neutral contract and the factory are exported: feature code
# must not import a provider client directly (see AGENTS.md), so the
# package root deliberately hides the concrete adapters.
__all__ = [
    "CompletionProvider",
    "CompletionRequest",
    "CompletionResponse",
    "create_cloud_completion_provider",
]
