"""Tests for score-run persistence.

These use the shared isolated-database fixtures: each case builds a real
job row via create_job, persists pipeline results through the service, and
asserts on the stored history plus the job's aggregate cache.
"""

import json
from collections.abc import Callable

import pytest
from pydantic import ValidationError
from sqlmodel import Session, select

from app.models.eligibility import (
    EligibilityReason,
    EligibilityReasonCode,
    EligibilityResult,
    UnknownField,
)
from app.models.job import Job
from app.models.score_run import ScoreLayerResultRow, ScoreRun
from app.models.scoring import (
    JobScoreResult,
    KeywordScoreResult,
    LayerOutcome,
    LlmScoreResult,
)
from app.services.score_storage import (
    MAX_FAILURE_DETAIL_CHARS,
    latest_score_run,
    save_score_run,
)
from app.services import score_storage


def eligible(unknowns: tuple[UnknownField, ...] = ()) -> EligibilityResult:
    return EligibilityResult(
        eligible=True,
        reasons=(),
        unknowns=unknowns,
        profile_role_name="Test Role",
        algorithm_version="1",
    )


def keyword_outcome(score: int = 80) -> LayerOutcome:
    return LayerOutcome(
        layer="keyword",
        status="success",
        result=KeywordScoreResult(
            layer="keyword",
            algorithm_version="1",
            score=score,
            explanation="Matched 4/5 keywords",
            title_score=score,
            description_score=0,
            matched_title_terms=("Python",),
            matched_description_terms=(),
            missing_terms=("Django",),
            excluded_terms_found=(),
        ),
        duration_ms=2,
    )


def scored_result(score: int = 80, **overrides) -> JobScoreResult:
    defaults = dict(
        status="scored",
        eligibility=eligible(),
        score=score,
        layer_outcomes=(keyword_outcome(score),),
        warnings=(),
        explanation="keyword: Matched 4/5 keywords",
        pipeline_version="1",
        weights_version="1",
    )
    defaults.update(overrides)
    return JobScoreResult(**defaults)


def test_scored_result_persists_run_layer_rows_and_cache(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=None)

    run = save_score_run(
        session,
        job_id=job.id,
        profile_id=job.profile_id,
        result=scored_result(score=80),
        duration_ms=42,
    )

    assert run.id is not None
    assert run.status == "scored"
    assert run.score == 80
    assert run.pipeline_version == "1"
    assert run.duration_ms == 42

    rows = session.exec(
        select(ScoreLayerResultRow).where(ScoreLayerResultRow.score_run_id == run.id)
    ).all()
    assert len(rows) == 1
    assert rows[0].layer == "keyword"
    assert rows[0].status == "success"
    assert rows[0].algorithm_version == "1"
    assert rows[0].score == 80
    keyword_details = json.loads(rows[0].details)
    assert keyword_details["matched_title_terms"] == ["Python"]
    assert keyword_details["title_score"] == 80

    session.refresh(job)
    assert job.score == 80
    assert job.score_reasoning == "keyword: Matched 4/5 keywords"


def test_rejected_run_persists_reasons_and_leaves_cache_untouched(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=77)
    rejected = JobScoreResult(
        status="rejected",
        eligibility=EligibilityResult(
            eligible=False,
            reasons=(
                EligibilityReason(
                    code=EligibilityReasonCode.excluded_keyword,
                    detail="blockchain",
                ),
            ),
            unknowns=(UnknownField.salary,),
            profile_role_name="Test Role",
            algorithm_version="1",
        ),
        warnings=("unchecked constraint: salary",),
        explanation="Rejected by eligibility filters",
        pipeline_version="1",
        weights_version="1",
    )

    run = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=rejected
    )

    assert run.status == "rejected"
    assert run.score is None
    assert json.loads(run.eligibility_reasons) == [
        {"code": "excluded_keyword", "detail": "blockchain"}
    ]
    assert json.loads(run.unknowns) == ["salary"]
    session.refresh(job)
    assert job.score == 77  # cache untouched


def test_skip_and_failure_outcomes_are_stored_with_diagnostics(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=None)
    result = scored_result(
        layer_outcomes=(
            keyword_outcome(),
            LayerOutcome(
                layer="semantic",
                status="skip",
                duration_ms=0,
                failure_code="unavailable",
                failure_detail="Ollama is down",
            ),
            LayerOutcome(
                layer="llm",
                status="failure",
                duration_ms=9,
                failure_code="layer_error",
                failure_detail="x" * 2000,
            ),
        ),
    )

    run = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=result
    )

    rows = session.exec(
        select(ScoreLayerResultRow)
        .where(ScoreLayerResultRow.score_run_id == run.id)
        .order_by(ScoreLayerResultRow.id)
    ).all()
    assert [row.status for row in rows] == ["success", "skip", "failure"]
    assert rows[1].failure_code == "unavailable"
    assert rows[1].score is None
    assert len(rows[2].failure_detail) == MAX_FAILURE_DETAIL_CHARS


def test_multiple_runs_coexist_and_latest_wins(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=None)

    first = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=scored_result(60)
    )
    second = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=scored_result(90)
    )

    runs = session.exec(select(ScoreRun).where(ScoreRun.job_id == job.id)).all()
    assert len(runs) == 2
    assert first.id != second.id

    latest = latest_score_run(session, job_id=job.id, profile_id=job.profile_id)
    assert latest is not None
    assert latest.id == second.id
    session.refresh(job)
    assert job.score == 90


def test_aggregate_score_0_is_persisted_and_cached(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=None)

    save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=scored_result(0)
    )

    session.refresh(job)
    assert job.score == 0


def test_llm_layer_extras_are_extracted_without_importing_layers(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=None)
    result = scored_result(
        layer_outcomes=(
            keyword_outcome(),
            LayerOutcome(
                layer="llm",
                status="success",
                result=LlmScoreResult(
                    layer="llm",
                    algorithm_version="1",
                    score=70,
                    explanation="Good fit",
                    model="qwen2.5:7b",
                    prompt_version="1",
                    duration_ms=1200,
                    attempts=1,
                    guard_flag_codes=(),
                ),
                duration_ms=1250,
            ),
        ),
    )

    run = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=result
    )

    rows = session.exec(
        select(ScoreLayerResultRow)
        .where(ScoreLayerResultRow.score_run_id == run.id)
        .order_by(ScoreLayerResultRow.id)
    ).all()
    assert rows[0].model is None  # keyword layer has no model identity
    assert rows[1].model == "qwen2.5:7b"
    assert rows[1].prompt_version == "1"
    llm_details = json.loads(rows[1].details)
    assert llm_details["attempts"] == 1
    assert llm_details["duration_ms"] == 1200
    assert llm_details["guard_flag_codes"] == []


def test_missing_job_rolls_back_the_whole_run(session: Session) -> None:
    with pytest.raises(ValueError):
        save_score_run(session, job_id=99999, profile_id=1, result=scored_result())

    assert session.exec(select(ScoreRun)).all() == []
    assert session.exec(select(ScoreLayerResultRow)).all() == []


def test_profile_mismatch_is_rejected_before_cache_or_history_changes(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=45)

    with pytest.raises(ValueError, match="belongs to profile"):
        save_score_run(
            session,
            job_id=job.id,
            profile_id=job.profile_id + 1000,
            result=scored_result(),
        )

    assert session.exec(select(ScoreRun)).all() == []
    session.refresh(job)
    assert job.score == 45


def test_unexpected_layer_translation_failure_rolls_back_and_cleans_session(
    session: Session,
    create_job: Callable[..., Job],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = create_job(score=45)

    def fail_layer_translation(score_run_id: int, outcome: LayerOutcome) -> None:
        raise RuntimeError("translation failed")

    monkeypatch.setattr(score_storage, "_layer_row", fail_layer_translation)

    with pytest.raises(RuntimeError, match="translation failed"):
        save_score_run(
            session,
            job_id=job.id,
            profile_id=job.profile_id,
            result=scored_result(),
        )

    assert session.exec(select(ScoreRun)).all() == []
    assert session.exec(select(ScoreLayerResultRow)).all() == []
    session.refresh(job)
    assert job.score == 45


def test_persistence_models_reject_internally_inconsistent_results() -> None:
    with pytest.raises(ValidationError, match="cannot include a result"):
        LayerOutcome(
            layer="semantic",
            status="failure",
            result=keyword_outcome().result,
            duration_ms=1,
            failure_code="layer_error",
        )

    with pytest.raises(ValidationError, match="requires an ineligible"):
        JobScoreResult(
            status="rejected",
            eligibility=eligible(),
            explanation="not actually rejected",
            pipeline_version="1",
            weights_version="1",
        )

    with pytest.raises(ValidationError, match="same name"):
        LayerOutcome(
            layer="semantic",
            status="success",
            result=keyword_outcome().result,
            duration_ms=1,
        )


def test_failed_run_is_persisted_without_touching_cache(
    session: Session,
    create_job: Callable[..., Job],
) -> None:
    job = create_job(score=55)
    failed = JobScoreResult(
        status="failed",
        eligibility=eligible(),
        layer_outcomes=(
            LayerOutcome(
                layer="semantic",
                status="skip",
                duration_ms=0,
                failure_code="unavailable",
                failure_detail="down",
            ),
        ),
        warnings=("layer semantic skipped: down",),
        explanation="No score layers succeeded, so no aggregate score exists",
        pipeline_version="1",
        weights_version="1",
    )

    run = save_score_run(
        session, job_id=job.id, profile_id=job.profile_id, result=failed
    )

    assert run.status == "failed"
    assert run.score is None
    session.refresh(job)
    assert job.score == 55


def test_sql_schema_replays_and_accepts_a_zero_score() -> None:
    """The recreatable SQL script must accept the widened 0-100 score range."""
    import sqlite3
    from pathlib import Path

    schema = Path("sql/hunter-agent.sql").read_text()
    with sqlite3.connect(":memory:") as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema)
        connection.execute(
            "INSERT INTO profiles (role_name, salary_min, match_threshold, active)"
            " VALUES ('Role', 0, 80, 1)"
        )
        connection.execute("INSERT INTO companies (name) VALUES ('ACME')")
        connection.execute("INSERT INTO locations (name) VALUES ('Remote')")
        connection.execute("INSERT INTO sources (name) VALUES ('adzuna')")
        connection.execute(
            "INSERT INTO jobs (profile_id, title, company_id, location_id,"
            " source_id, scraped_at, score) VALUES (1, 'Dev', 1, 1, 1,"
            " '2026-07-08', 0)"
        )
        connection.execute(
            "INSERT INTO score_runs (job_id, profile_id, pipeline_version,"
            " weights_version, status, score) VALUES (1, 1, '1', '1',"
            " 'scored', 0)"
        )
        connection.execute(
            "INSERT INTO score_layer_results (score_run_id, layer, status,"
            " score) VALUES (1, 'keyword', 'success', 0)"
        )
        connection.commit()

        # The reset script must also replay over populated history while
        # foreign keys are enabled, which requires child-first drop order.
        connection.executescript(schema)
