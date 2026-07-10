"""Shared schema and loader for the labeled scoring-evaluation fixture.

Both the always-on schema test and the opt-in comparison harness import
from here, so the fixture's shape is defined exactly once. The fixture
answers one question — "do the layers rank jobs the way I would?" — with
a human 0-2 relevance grade per job (HNTR-1).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.profile import LocationType, Profile
from app.services.eligibility import check_eligibility
from app.services.keyword_scoring import score_job_keywords
from app.services.profiles import ProfileDetail
from app.services.scoring_pipeline import (
    LAYER_WEIGHTS,
    KeywordScoreLayer,
    ScoreJobInput,
    ScoreLayerRegistry,
    score_job,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "scoring_eval.json"

EvalBucket = Literal["clear_match", "clear_reject", "ambiguous", "hard_filter"]

# Aggregate scores map onto the human 0-2 grades with these documented
# cut points; eligibility-rejected jobs always bucket to 0.
LABEL_CUTS = {"low_below": 40, "high_at_least": 70}


class EvalProfile(BaseModel):
    """The one scoring profile every fixture job is judged against."""

    model_config = ConfigDict(extra="forbid")

    role_name: str = Field(min_length=1)
    keywords: list[str] = Field(min_length=1)
    exclude_keywords: list[str] = Field(default_factory=list)
    # Typed with the real enum so a bad arrangement fails schema
    # validation here, not deep inside a later eligibility test.
    location_types: list[LocationType] = Field(min_length=1)
    salary_min: int = Field(default=0, ge=0)

    def to_profile_detail(self) -> ProfileDetail:
        """Build the structural ScoringProfile view, no database needed."""
        return ProfileDetail(
            profile=Profile(
                role_name=self.role_name,
                salary_min=self.salary_min,
                match_threshold=80,
                active=True,
            ),
            location_types=tuple(self.location_types),
            keywords=tuple(self.keywords),
            exclude_keywords=tuple(self.exclude_keywords),
            source_queries=(),
        )


class EvalJob(BaseModel):
    """One labeled job: inputs, expectation bucket, and the human grade."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, pattern=r"^[a-z0-9-]+$")
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    location: str | None = None
    bucket: EvalBucket
    human_label: Literal[0, 1, 2]
    note: str = Field(min_length=1)

    @field_validator("note")
    @classmethod
    def note_is_one_line(cls, note: str) -> str:
        if "\n" in note:
            raise ValueError("notes must stay one line")
        return note


class EvalFixture(BaseModel):
    """The whole labeled corpus."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    profile: EvalProfile
    jobs: list[EvalJob] = Field(min_length=25)

    @model_validator(mode="after")
    def ids_are_unique_and_buckets_covered(self) -> "EvalFixture":
        ids = [job.id for job in self.jobs]
        if len(ids) != len(set(ids)):
            raise ValueError("job ids must be unique")
        buckets = {job.bucket for job in self.jobs}
        missing = {"clear_match", "clear_reject", "ambiguous", "hard_filter"} - buckets
        if missing:
            raise ValueError(f"fixture is missing buckets: {sorted(missing)}")
        return self


def load_fixture() -> EvalFixture:
    """Parse and validate the labeled corpus from disk."""
    return EvalFixture.model_validate(json.loads(FIXTURE_PATH.read_text()))


@dataclass(frozen=True)
class DeterministicSnapshot:
    """Stable eligibility, keyword, and composed-pipeline output for one job."""

    job_id: str
    eligibility_json: str
    keyword_json: str
    pipeline_json: str


def deterministic_snapshots(fixture: EvalFixture) -> list[DeterministicSnapshot]:
    """Evaluate every deterministic layer with a fresh registry."""
    profile = fixture.profile.to_profile_detail()
    registry = ScoreLayerRegistry()
    registry.register(
        KeywordScoreLayer(), weight=LAYER_WEIGHTS["keyword"], required=True
    )
    return [_deterministic_snapshot(job, profile, registry) for job in fixture.jobs]


def assert_deterministic_runs_equal(
    first: list[DeterministicSnapshot],
    second: list[DeterministicSnapshot],
) -> None:
    """Fail with the changed fixture ids when deterministic output drifts."""
    if first == second:
        return
    first_by_id = {snapshot.job_id: snapshot for snapshot in first}
    second_by_id = {snapshot.job_id: snapshot for snapshot in second}
    changed = sorted(
        job_id
        for job_id in first_by_id.keys() | second_by_id.keys()
        if first_by_id.get(job_id) != second_by_id.get(job_id)
    )
    raise AssertionError(f"deterministic output changed for: {', '.join(changed)}")


def _deterministic_snapshot(
    job: EvalJob,
    profile: ProfileDetail,
    registry: ScoreLayerRegistry,
) -> DeterministicSnapshot:
    eligibility = check_eligibility(
        title=job.title,
        description=job.description,
        location=job.location,
        profile=profile,
    )
    keyword = score_job_keywords(job.title, job.description, profile)
    result = asyncio.run(
        score_job(
            ScoreJobInput(
                title=job.title,
                description=job.description,
                location=job.location,
            ),
            profile,
            registry=registry,
        )
    )
    pipeline_payload = result.model_dump(mode="json")
    for outcome in pipeline_payload["layer_outcomes"]:
        # Wall-clock timing is operational metadata, not scoring output.
        outcome.pop("duration_ms", None)
    return DeterministicSnapshot(
        job_id=job.id,
        eligibility_json=json.dumps(
            eligibility.model_dump(mode="json"), sort_keys=True
        ),
        keyword_json=json.dumps(keyword.model_dump(mode="json"), sort_keys=True),
        pipeline_json=json.dumps(pipeline_payload, sort_keys=True),
    )
