"""Always-on schema validation for the labeled scoring fixture (HNTR-1).

The comparison harness itself is opt-in (it can call local models), but
the corpus must never rot: this suite runs with every ``uv run pytest``
and fails when the fixture drifts from its schema or loses coverage.
Shape rules (ids, labels, notes, size floor) live in the Pydantic models
in scoring_eval.py; these tests only add checks the schema cannot state.
"""

from dataclasses import replace

import pytest

from app.services.eligibility import check_eligibility
from scoring_eval import (
    EvalFixture,
    assert_deterministic_runs_equal,
    deterministic_snapshots,
    load_fixture,
)


def test_fixture_parses_and_validates() -> None:
    # Parsing is the assertion: load_fixture raises on any schema drift.
    load_fixture()


def test_every_bucket_is_well_represented() -> None:
    fixture = load_fixture()

    by_bucket: dict[str, int] = {}
    for job in fixture.jobs:
        by_bucket[job.bucket] = by_bucket.get(job.bucket, 0) + 1

    for bucket in ("clear_match", "clear_reject", "ambiguous", "hard_filter"):
        assert by_bucket.get(bucket, 0) >= 3, f"bucket {bucket} is underrepresented"


def test_bucket_labels_are_coherent() -> None:
    """The buckets encode expectations the labels must not contradict."""
    fixture = load_fixture()

    for job in fixture.jobs:
        if job.bucket == "clear_match":
            assert job.human_label == 2, f"{job.id}: clear matches are labeled 2"
        elif job.bucket in ("clear_reject", "hard_filter"):
            assert job.human_label == 0, f"{job.id}: rejects are labeled 0"
        else:
            assert job.human_label == 1, f"{job.id}: ambiguous jobs are labeled 1"


def test_hard_filter_jobs_are_actually_rejected_by_eligibility() -> None:
    """A hard-filter case that eligibility accepts is a mislabeled fixture."""
    fixture = load_fixture()
    profile = fixture.profile.to_profile_detail()

    for job in fixture.jobs:
        result = check_eligibility(
            title=job.title,
            description=job.description,
            location=job.location,
            profile=profile,
        )
        if job.bucket == "hard_filter":
            assert not result.eligible, f"{job.id} should be rejected"
        else:
            assert result.eligible, f"{job.id} should pass hard filters"


def test_duplicate_ids_are_rejected() -> None:
    fixture = load_fixture()
    payload = fixture.model_dump()
    payload["jobs"].append(payload["jobs"][0])

    with pytest.raises(ValueError, match="unique"):
        EvalFixture.model_validate(payload)


def test_deterministic_layers_match_across_fresh_runs() -> None:
    fixture = load_fixture()

    assert_deterministic_runs_equal(
        deterministic_snapshots(fixture),
        deterministic_snapshots(fixture),
    )


def test_deterministic_comparison_rejects_divergence() -> None:
    first = deterministic_snapshots(load_fixture())
    second = [*first]
    second[0] = replace(second[0], keyword_json='{"score": -1}')

    with pytest.raises(AssertionError, match=second[0].job_id):
        assert_deterministic_runs_equal(first, second)
