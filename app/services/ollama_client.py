"""Thin HTTP client for scoring resume facts with a local Ollama model.

This module owns everything Ollama-specific: the endpoint, the prompt
template, and response validation. The tailoring service only sees
``ScoringResult`` values, so a different provider can replace this client
later without touching the tailoring logic.
"""

import json
import re
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import PROJECT_ROOT, load_config
from app.models.config import OllamaConfig


SCORING_PROMPT_PATH = PROJECT_ROOT / "app" / "prompts" / "resume_scoring.txt"

# The model occasionally fails or the server is simply not running. A neutral
# fallback score keeps a tailoring run usable instead of crashing it; the
# is_fallback flag lets callers and the UI mark those items for review.
FALLBACK_SCORE = 50

DEFAULT_TIMEOUT_SECONDS = 30.0

# Scraped job descriptions are untrusted and can be arbitrarily long. Cap the
# text that reaches the prompt; keyword-dense openings carry most signal.
# TODO(HNTR-9): add real prompt-injection guarding for untrusted job content.
MAX_JOB_DESCRIPTION_CHARS = 4000


@dataclass(frozen=True)
class ScoringResult:
    """One relevance judgement for one resume fact."""

    score: int
    reasoning: str
    is_fallback: bool = False


@lru_cache(maxsize=1)
def load_scoring_prompt() -> tuple[str, str]:
    """Return (prompt_version, template) from the versioned prompt file.

    The version travels into resume_tailor_runs so old scores remain
    attributable to the exact prompt that produced them.
    """
    raw_prompt = SCORING_PROMPT_PATH.read_text(encoding="utf-8")
    header, _, template = raw_prompt.partition("---")
    version = header.replace("PROMPT_VERSION:", "").strip()
    if not version:
        raise ValueError(f"{SCORING_PROMPT_PATH} is missing a PROMPT_VERSION header")
    return version, template.strip()


class OllamaClient:
    """Scores resume facts against a job via the local Ollama HTTP API."""

    def __init__(
        self,
        config: OllamaConfig | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.config = config or load_config().ollama
        self.timeout = timeout
        self.prompt_version, self._prompt_template = load_scoring_prompt()
        self._generate_url = f"{str(self.config.base_url).rstrip('/')}/api/generate"
        # One pooled connection for the whole scoring run instead of a new
        # TCP handshake per scored item.
        self._http = httpx.Client(timeout=timeout)

    @property
    def model_name(self) -> str:
        return self.config.scorer.model

    def score_item(
        self,
        *,
        item_content: str,
        job_title: str,
        job_description: str,
    ) -> ScoringResult:
        """Return a 0-100 relevance score, or a neutral fallback on failure."""
        # Substitute all placeholders in one pass so a value containing a
        # literal placeholder string cannot be re-expanded by a later step.
        substitutions = {
            "{item_content}": item_content,
            "{job_title}": job_title,
            "{job_description}": job_description[:MAX_JOB_DESCRIPTION_CHARS],
        }
        prompt = re.sub(
            r"\{(?:item_content|job_title|job_description)\}",
            lambda match: substitutions[match.group(0)],
            self._prompt_template,
        )

        request_body = {
            "model": self.config.scorer.model,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {
                "temperature": self.config.scorer.temperature,
                "num_predict": self.config.scorer.max_tokens,
            },
        }

        try:
            response = self._http.post(self._generate_url, json=request_body)
            response.raise_for_status()
            model_output = response.json()["response"]
        except (httpx.HTTPError, json.JSONDecodeError, KeyError) as error:
            return self._fallback(f"Ollama unavailable: {type(error).__name__}")

        return self._parse_model_output(model_output)

    def _parse_model_output(self, model_output: str) -> ScoringResult:
        """Validate the model's JSON answer; malformed output falls back."""
        try:
            data = json.loads(model_output)
            score = int(data["score"])
            reasoning = str(data["reasoning"])
        except KeyError, TypeError, ValueError:
            # json.JSONDecodeError is a ValueError subclass, so this covers it.
            return self._fallback("Malformed scoring response from model")

        if not 0 <= score <= 100:
            return self._fallback(f"Model returned out-of-range score {score}")

        return ScoringResult(score=score, reasoning=reasoning)

    def _fallback(self, reason: str) -> ScoringResult:
        return ScoringResult(
            score=FALLBACK_SCORE,
            reasoning=f"{reason}; assigned neutral fallback score",
            is_fallback=True,
        )
