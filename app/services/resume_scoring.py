"""Local resume-item relevance scoring through the completion protocol."""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.config import PROJECT_ROOT
from app.services.ai.completion import CompletionProvider, CompletionRequest
from app.services.ai.errors import AIProviderError
from app.services.prompt_guard import GuardedSection, build_guarded_payload


SCORING_PROMPT_PATH = PROJECT_ROOT / "app" / "prompts" / "resume_scoring.txt"
FALLBACK_SCORE = 50


class ScoringResult(BaseModel):
    """One validated local relevance judgement."""

    model_config = ConfigDict(frozen=True)

    score: int = Field(ge=0, le=100)
    reasoning: str = Field(min_length=1)
    is_fallback: bool = False


@lru_cache(maxsize=None)
def load_versioned_prompt(path: Path) -> tuple[str, str]:
    """Load a trusted prompt body and its auditable version header."""
    raw_prompt = path.read_text(encoding="utf-8")
    header, separator, template = raw_prompt.partition("---")
    version = header.replace("PROMPT_VERSION:", "").strip()
    if not separator or not version or not template.strip():
        raise ValueError(f"{path} must contain a prompt version and body")
    return version, template.strip()


class ResumeItemScorer:
    """Score resume facts locally while keeping job text behind the guard."""

    def __init__(self, provider: CompletionProvider) -> None:
        self.provider = provider
        self.prompt_version, self._instructions = load_versioned_prompt(
            SCORING_PROMPT_PATH
        )

    @property
    def model_name(self) -> str:
        return self.provider.model

    async def score_item(
        self,
        *,
        item_content: str,
        guarded_job: tuple[GuardedSection, GuardedSection],
    ) -> ScoringResult:
        """Return a local score, or a visibly marked neutral fallback."""
        payload = build_guarded_payload(
            f"{self._instructions}\n\nTrusted resume fact:\n{item_content}",
            guarded_job,
        )
        try:
            response = await self.provider.complete(
                CompletionRequest(
                    prompt=payload.render_prompt(),
                    response_schema=ScoringResult.model_json_schema(),
                )
            )
            result = ScoringResult.model_validate_json(response.text)
        except (AIProviderError, ValidationError, ValueError, TypeError) as error:
            return ScoringResult(
                score=FALLBACK_SCORE,
                reasoning=(
                    f"Local scorer unavailable ({type(error).__name__}); "
                    "assigned neutral fallback score"
                ),
                is_fallback=True,
            )
        return result
