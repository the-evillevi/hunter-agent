"""Always-on schema validation for the labeled scoring fixture (HNTR-1).

The comparison harness itself is opt-in (it can call local models), but
the corpus must never rot: this suite runs with every ``uv run pytest``
and fails when the fixture drifts from its schema or loses coverage.
"""

import pytest

from scoring_eval import EvalFixture, load_fixture


def test_fixture_parses_and_validates() -> None:
    fixture = load_fixture()

    assert fixture.schema_version == 1
    assert len(fixture.jobs) >= 25


def test_fixture_covers_all_four_buckets_with_labels_and_notes() -> None:
    fixture = load_fixture()

    by_bucket: dict[str, int] = {}
    for job in fixture.jobs:
        by_bucket[job.bucket] = by_bucket.get(job.bucket, 0) + 1
        assert job.note.strip(), f"{job.id} has a blank note"

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
    from app.services.eligibility import check_eligibility

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
