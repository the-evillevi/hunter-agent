"""Shared fakes for the generator-critic tailoring tests.

One canonical completion-protocol fake and one set of responders, imported
by the tailor, route, and performance test modules so the fake stack
cannot drift apart per file. Responders take only the request; sync and
async callables both work.
"""

import inspect
import json
from pathlib import Path
from typing import Any

from app.services.ai.completion import CompletionRequest, CompletionResponse
from app.services.resume_scoring import ResumeItemScorer
from app.services.resume_tailor import ResumeTailor


RESUME_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_sample.json"


class FakeCompletionProvider:
    """Protocol-compatible provider that records every in-memory request."""

    def __init__(
        self,
        provider_name: str,
        model: str,
        responder,
        *,
        response_model: str | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model = model
        self.response_model = response_model or model
        self.responder = responder
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        text = self.responder(request)
        if inspect.isawaitable(text):
            text = await text
        return CompletionResponse(
            text=text,
            provider=self.provider_name,
            model=self.response_model,
            duration_ms=1,
            finish_reason="stop",
            raw_usage={"fake_tokens": 1},
        )


def source_document(prompt: str) -> dict[str, Any]:
    """Extract the trusted resume JSON a prompt carries."""
    for marker in ("TRUSTED_RESUME_JSON:", "TRUSTED_SOURCE_JSON:"):
        if marker in prompt:
            tail = prompt.split(marker, 1)[1]
            document, _end = json.JSONDecoder().raw_decode(tail)
            return document
    raise AssertionError("trusted source JSON marker missing")


def local_responder(request: CompletionRequest) -> str:
    """Deterministic relevance judgement: IBM items are relevant."""
    score = 90 if "IBM" in request.prompt else 20
    reasoning = "Directly relevant." if score == 90 else "Unrelated to this job."
    return json.dumps({"score": score, "reasoning": reasoning})


def generator_responder(request: CompletionRequest) -> str:
    """Echo back every eligible source item as the tailored draft."""
    source = source_document(request.prompt)
    draft = {
        "sections": [
            {
                "section_type": section["section_type"],
                "title": section["title"],
                "items": [
                    {
                        "source_item_id": item["source_item_id"],
                        "content_json": json.dumps(item["content"]),
                    }
                    for item in section["items"]
                    if item["eligible_for_tailoring"]
                ],
            }
            for section in source["sections"]
            if any(item["eligible_for_tailoring"] for item in section["items"])
        ]
    }
    return json.dumps(draft)


def critic_responder(request: CompletionRequest) -> str:
    """A satisfied critic: no findings, so no revision pass runs."""
    return json.dumps(
        {
            "fit_summary": "The supported evidence fits the role.",
            "missing_evidence": [],
            "overclaims": [],
            "required_changes": [],
        }
    )


def build_tailor(
    *,
    local: FakeCompletionProvider | None = None,
    generator: FakeCompletionProvider | None = None,
    critic: FakeCompletionProvider | None = None,
) -> ResumeTailor:
    """Assemble a fully faked tailor; pass overrides to break one role."""
    local_provider = local or FakeCompletionProvider(
        "ollama", "local-test-model", local_responder
    )
    return ResumeTailor(
        scorer=ResumeItemScorer(local_provider),
        generator=generator
        or FakeCompletionProvider("openai", "gpt-5.5", generator_responder),
        critic=critic or FakeCompletionProvider("openai", "gpt-5.5", critic_responder),
    )
