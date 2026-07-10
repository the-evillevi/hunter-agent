"""Optional structured LLM score layer.

This layer (HNTR-50) asks a local model — through the provider-neutral
completion protocol's Ollama scorer role, per the 2026-07-07 decision that
scoring never uses cloud models — how well a job fits a profile, and
validates the answer against a strict schema. It composes the two
boundaries below it: every piece of job text passes the HNTR-9 prompt
guard first, and every model call goes through the HNTR-14 completion
protocol. The layer is optional by contract: provider failures degrade to
a pipeline skip and validation failures to an explicit layer failure,
neither of which touches deterministic scoring.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.models.scoring import LlmScoreResult
from app.services.ai.completion import CompletionProvider, CompletionRequest
from app.services.ai.errors import AIProviderError
from app.services.prompt_guard import (
    GuardedPayload,
    build_guarded_payload,
    guard_untrusted_text,
)
from app.services.scoring_pipeline import (
    ScoreJobInput,
    ScoreLayerUnavailableError,
    ScoringProfile,
)


LLM_LAYER_NAME = "llm"

# Bump when prompt text, schema, or validation behavior changes, so
# persisted scores can distinguish results across prompt revisions.
LLM_ALGORITHM_VERSION = "1"
LLM_PROMPT_VERSION = "1"

# One repair retry, then explicit failure: a local model that cannot
# produce valid JSON twice will not get better on a third identical ask.
MAX_ATTEMPTS = 2
MAX_PROFILE_SUMMARY_CHARS = 1000
MAX_REASONING_CHARS = 1000

SCORING_INSTRUCTIONS = (
    "You are scoring how well one job listing matches a target profile.\n"
    "Target profile: {profile_summary}\n"
    "Respond with JSON only: score is an integer from 0 (no match) to 100 "
    "(perfect match), reasoning is one short paragraph naming the decisive "
    "factors."
)

REPAIR_NOTE = (
    "\nYour previous reply was not valid against the required schema. "
    "Reply again with ONLY a JSON object of the form "
    '{"score": <integer 0-100>, "reasoning": "<short text>"}.'
)


class LlmScorePayload(BaseModel):
    """The exact shape the model must return; doubles as the JSON schema."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100, strict=True)
    reasoning: str = Field(min_length=1, max_length=MAX_REASONING_CHARS)

    @field_validator("reasoning")
    @classmethod
    def reasoning_must_not_be_blank(cls, reasoning: str) -> str:
        reasoning = reasoning.strip()
        if not reasoning:
            raise ValueError("reasoning must not be blank")
        return reasoning


# The schema is static; computing it once keeps the retry loop free of
# repeated work and gives tests one canonical object to assert against.
LLM_RESPONSE_SCHEMA = LlmScorePayload.model_json_schema()

# Validation errors can enumerate many constraint failures; bound what the
# failure diagnostics retain, matching the pipeline's failure-detail cap.
MAX_ERROR_DETAIL_CHARS = 500


class LlmScoreFailedError(Exception):
    """The model answered, but never produced a valid score.

    Carries enough identity for the pipeline's failure outcome and later
    persistence to explain which model and prompt version gave up.
    """

    def __init__(
        self,
        message: str,
        *,
        model: str,
        prompt_version: str,
        attempts: int,
        guard_flag_codes: tuple[str, ...],
    ) -> None:
        flags = ",".join(guard_flag_codes) or "none"
        super().__init__(
            f"model={model}; prompt_version={prompt_version}; attempts={attempts}; "
            f"guard_flags={flags}; {message}"
        )
        self.model = model
        self.prompt_version = prompt_version
        self.attempts = attempts
        self.guard_flag_codes = guard_flag_codes


class LlmScoreLayer:
    """Optional score layer backed by a completion provider.

    Implements the HNTR-11 ``ScoreLayer`` contract. The provider is
    injected, so tests use a fake and production wiring chooses the Ollama
    scorer role — or, if the evaluation harness ever justifies it, a
    different provider behind the same protocol.
    """

    name = LLM_LAYER_NAME

    def __init__(self, provider: CompletionProvider) -> None:
        self._provider = provider

    async def score(
        self,
        job: ScoreJobInput,
        profile: ScoringProfile,
    ) -> LlmScoreResult:
        """Ask the model for a structured score over guarded job text."""
        if not any(text and text.strip() for text in (job.title, job.description)):
            # Nothing to score: asking the model about an empty listing
            # wastes a call and invites hallucinated reasoning.
            raise ScoreLayerUnavailableError(
                "job has no text to score", code="no_input_text"
            )

        payload = self._build_payload(job, profile)
        # Role separation beats position-in-string: trusted instructions
        # ride the system role, fenced job text rides the user role.
        untrusted_prompt = payload.render_untrusted()
        guard_flag_codes = tuple(flag.code for flag in payload.flags)

        last_error = "no attempts made"
        total_duration_ms = 0
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = CompletionRequest(
                system_prompt=(
                    payload.instructions
                    if attempt == 1
                    else payload.instructions + REPAIR_NOTE
                ),
                prompt=untrusted_prompt,
                response_schema=LLM_RESPONSE_SCHEMA,
            )
            try:
                response = await self._provider.complete(request)
            except AIProviderError as error:
                # A dead or unreachable provider is not worth retrying on
                # the same request; the pipeline records a skip instead.
                flags = ",".join(guard_flag_codes) or "none"
                raise ScoreLayerUnavailableError(
                    "llm layer unavailable for "
                    f"{self._provider.provider_name}/{self._provider.model} "
                    f"with prompt {LLM_PROMPT_VERSION}; guard_flags={flags}: {error}"
                ) from error

            total_duration_ms += response.duration_ms

            try:
                parsed = LlmScorePayload.model_validate_json(response.text)
            except ValidationError as error:
                last_error = str(error)[:MAX_ERROR_DETAIL_CHARS]
                continue

            return LlmScoreResult(
                layer=self.name,
                algorithm_version=LLM_ALGORITHM_VERSION,
                score=parsed.score,
                explanation=parsed.reasoning,
                model=response.model,
                prompt_version=LLM_PROMPT_VERSION,
                duration_ms=total_duration_ms,
                attempts=attempt,
                guard_flag_codes=guard_flag_codes,
            )

        raise LlmScoreFailedError(
            f"model produced no valid score in {MAX_ATTEMPTS} attempts: {last_error}",
            model=self._provider.model,
            prompt_version=LLM_PROMPT_VERSION,
            attempts=MAX_ATTEMPTS,
            guard_flag_codes=guard_flag_codes,
        )

    def _build_payload(
        self,
        job: ScoreJobInput,
        profile: ScoringProfile,
    ) -> GuardedPayload:
        """Compose trusted instructions with guarded job text.

        Profile data comes from validated local config, so it belongs in
        the trusted instruction section; job title and description come
        from external providers and are always fenced.
        """
        profile_summary = build_profile_summary(profile)
        instructions = SCORING_INSTRUCTIONS.format(profile_summary=profile_summary)

        sections = [
            guard_untrusted_text(text, label=label)
            for label, text in (
                ("job_title", job.title),
                ("job_description", job.description),
            )
            if text and text.strip()
        ]
        return build_guarded_payload(instructions, sections)


def build_profile_summary(profile: ScoringProfile) -> str:
    """Return a bounded trusted profile representation for the model."""
    summary = f"{profile.profile.role_name} ({', '.join(profile.keywords)})"
    return summary[:MAX_PROFILE_SUMMARY_CHARS]
