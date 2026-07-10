"""Provider-neutral completion request/response contract.

This module exists so scoring (HNTR-50) and CV tailoring (HNTR-56) talk to
"a completion provider" rather than to Ollama, Anthropic, or OpenAI. The
shapes carry structured metadata (identity, timing, finish state, raw
usage) and support structured-output requests through a plain JSON schema
dict, so no feature-specific types ever leak into this boundary and no
credentials appear on the protocol surface — adapters own auth.
"""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


class CompletionRequest(BaseModel):
    """One provider-neutral completion call.

    ``response_schema`` is a plain JSON-schema dict: local (Ollama format
    parameter) and cloud (native structured outputs) providers can both
    satisfy it without protocol changes. ``temperature`` and ``max_tokens``
    left as None mean "use the adapter's configured role defaults".
    """

    model_config = ConfigDict(frozen=True)

    prompt: str = Field(min_length=1)
    system_prompt: str | None = None
    response_schema: dict[str, Any] | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0)


class CompletionResponse(BaseModel):
    """What every adapter must report back alongside the completion text.

    ``raw_usage`` keeps provider-native counters unmapped on purpose: the
    evaluation harness can compare providers without this contract having
    to guess a universal usage schema up front.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    duration_ms: int = Field(ge=0)
    finish_reason: Literal["stop", "length", "unknown"]
    raw_usage: dict[str, Any] = Field(default_factory=dict)


class CompletionProvider(Protocol):
    """What feature code may assume about any completion implementation."""

    provider_name: str
    model: str

    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...
