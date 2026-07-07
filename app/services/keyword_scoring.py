"""Deterministic keyword scoring for job listings.

This is the first relevance layer of the scoring pipeline (HNTR-13): it
compares a job's title and description against a profile's keywords with
plain token matching — no models, no network, no randomness — so a score
can always be reproduced and explained. Eligibility rejection is owned by
HNTR-49; this layer only reports excluded terms it happens to see.
"""

import re

from app.models.config import ProfileConfig
from app.models.scoring import KeywordScoreResult


KEYWORD_LAYER_NAME = "keyword"

# Bump this when matching or weighting behavior changes, so persisted scores
# (HNTR-10) can distinguish results produced by different algorithm versions.
KEYWORD_ALGORITHM_VERSION = "1"

# Field weights are versioned code defaults, not config: profile authors tune
# keywords, while weighting is part of the algorithm's identity. A title hit
# is worth twice a description hit because titles are short and intentional.
TITLE_WEIGHT = 2
DESCRIPTION_WEIGHT = 1

# Tokens keep "+" and "#" so "c++" and "c#" survive normalization; every
# other symbol becomes a token boundary ("Node.js" -> ["node", "js"]).
TOKEN_RE = re.compile(r"[a-z0-9+#]+")

# Collapsed n-grams let hyphen/space variants match each other:
# "full-stack" tokenizes to ["full", "stack"] and collapses to "fullstack".
MAX_COLLAPSED_NGRAM = 3


def tokenize(text: str | None) -> list[str]:
    """Lowercase text and split it into comparable word tokens."""
    if not text:
        return []
    return TOKEN_RE.findall(text.casefold())


def collapsed_ngrams(tokens: list[str]) -> set[str]:
    """Join short runs of adjacent tokens so spelling variants can match.

    This is how "full stack", "full-stack", and "fullstack" all find each
    other without a maintained alias map.
    """
    joined: set[str] = set()
    for size in range(1, MAX_COLLAPSED_NGRAM + 1):
        for start in range(len(tokens) - size + 1):
            joined.add("".join(tokens[start : start + size]))
    return joined


class FieldIndex:
    """Pre-tokenized view of one text field (title or description)."""

    def __init__(self, text: str | None) -> None:
        self.tokens = tokenize(text)
        self.collapsed = collapsed_ngrams(self.tokens)

    def contains(self, term: str) -> bool:
        """Return whether the term appears as whole tokens in this field.

        Whole-token comparison avoids partial-word traps: the keyword
        "Java" must not match "JavaScript". Multi-word keywords match as a
        contiguous token phrase or as one collapsed variant.
        """
        term_tokens = tokenize(term)
        if not term_tokens:
            return False
        if "".join(term_tokens) in self.collapsed:
            return True
        phrase_size = len(term_tokens)
        return any(
            self.tokens[start : start + phrase_size] == term_tokens
            for start in range(len(self.tokens) - phrase_size + 1)
        )


def score_job_keywords(
    title: str | None,
    description: str | None,
    profile: ProfileConfig,
) -> KeywordScoreResult:
    """Score job text against one profile's keywords, deterministically.

    Each keyword contributes its best single match: TITLE_WEIGHT when found
    in the title, DESCRIPTION_WEIGHT when found only in the description,
    nothing otherwise. The aggregate is normalized against the best possible
    outcome (every keyword in the title), so empty or missing text can only
    lower the score, never inflate it.
    """
    title_index = FieldIndex(title)
    description_index = FieldIndex(description)

    matched_title: list[str] = []
    matched_description: list[str] = []
    missing: list[str] = []

    for keyword in profile.keywords:
        if title_index.contains(keyword):
            matched_title.append(keyword)
        elif description_index.contains(keyword):
            matched_description.append(keyword)
        else:
            missing.append(keyword)

    # Excluded terms are reported for transparency only; rejecting the job
    # for a hard constraint is HNTR-49's decision, not this layer's.
    excluded_found = [
        term
        for term in profile.exclude_keywords
        if title_index.contains(term) or description_index.contains(term)
    ]

    keyword_count = len(profile.keywords)
    weighted_total = TITLE_WEIGHT * len(matched_title) + DESCRIPTION_WEIGHT * len(
        matched_description
    )
    score = round(100 * weighted_total / (TITLE_WEIGHT * keyword_count))

    title_score = round(100 * len(matched_title) / keyword_count)
    description_score = round(100 * len(matched_description) / keyword_count)

    matched_count = len(matched_title) + len(matched_description)
    explanation = (
        f"Matched {matched_count}/{keyword_count} keywords "
        f"({len(matched_title)} in title, {len(matched_description)} in description)"
    )
    if excluded_found:
        explanation += f"; excluded terms present: {', '.join(excluded_found)}"

    return KeywordScoreResult(
        layer=KEYWORD_LAYER_NAME,
        algorithm_version=KEYWORD_ALGORITHM_VERSION,
        score=score,
        explanation=explanation,
        title_score=title_score,
        description_score=description_score,
        matched_title_terms=tuple(matched_title),
        matched_description_terms=tuple(matched_description),
        missing_terms=tuple(missing),
        excluded_terms_found=tuple(excluded_found),
    )
