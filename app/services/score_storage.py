"""Persistence for scoring pipeline results.

This service (HNTR-10) turns one JobScoreResult into an append-only
score_runs row plus one score_layer_results row per layer outcome, all in
a single transaction, and refreshes the job's aggregate score/reasoning
cache only for scored runs. History is never updated or deleted: rescoring
a job adds a new run, so the evaluation harness (HNTR-1) can compare
algorithm, model, and prompt versions over time.
"""

import json

from sqlmodel import Session, select

from app.models.job import Job
from app.models.score_run import ScoreLayerResultRow, ScoreRun
from app.models.scoring import JobScoreResult, LayerOutcome

# The pipeline already bounds failure detail; re-truncating here with the
# same shared cap is defense in depth for rows written by future callers.
from app.services.scoring_pipeline import MAX_FAILURE_DETAIL_CHARS


def save_score_run(
    session: Session,
    *,
    job_id: int,
    profile_id: int,
    result: JobScoreResult,
    duration_ms: int | None = None,
) -> ScoreRun:
    """Persist one pipeline result and refresh the job's aggregate cache.

    The run row, its layer rows, and the job cache update commit together:
    a latest aggregate can never become visible without the detail rows
    that explain it. Rejected and failed runs are persisted too — knowing
    why a job was rejected or why no score exists is audit data — but they
    leave the job's cached aggregate untouched.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} does not exist")
    if job.profile_id != profile_id:
        raise ValueError(
            f"job {job_id} belongs to profile {job.profile_id}, not {profile_id}"
        )

    run = ScoreRun(
        job_id=job_id,
        profile_id=profile_id,
        pipeline_version=result.pipeline_version,
        weights_version=result.weights_version,
        status=result.status,
        score=result.score,
        explanation=result.explanation,
        eligibility_reasons=json.dumps(
            [reason.model_dump(mode="json") for reason in result.eligibility.reasons]
        ),
        unknowns=json.dumps([unknown.value for unknown in result.eligibility.unknowns]),
        warnings=json.dumps(list(result.warnings)),
        duration_ms=duration_ms,
    )
    session.add(run)
    try:
        # Flush assigns the run id for the layer rows without committing
        # yet, keeping the whole write inside one transaction.
        session.flush()
        if run.id is None:  # pragma: no cover - flush always assigns the PK
            raise RuntimeError("score run id missing after flush")

        session.add_all(
            _layer_row(run.id, outcome) for outcome in result.layer_outcomes
        )

        if result.status == "scored":
            job.score = result.score
            job.score_reasoning = result.explanation
            session.add(job)

        session.commit()
    except Exception:
        # Any failure after the run is staged must leave the session clean;
        # otherwise a later caller could accidentally commit a partial run.
        session.rollback()
        raise

    session.refresh(run)
    return run


def latest_score_run(
    session: Session,
    *,
    job_id: int,
    profile_id: int,
) -> ScoreRun | None:
    """Return the newest run for one job/profile pair, if any exists.

    Ties on created_at (SQLite second precision) resolve by highest id,
    which is insertion order.
    """
    statement = (
        select(ScoreRun)
        .where(ScoreRun.job_id == job_id, ScoreRun.profile_id == profile_id)
        .order_by(ScoreRun.created_at.desc(), ScoreRun.id.desc())  # type: ignore[union-attr]
        .limit(1)
    )
    return session.exec(statement).first()


def _layer_row(score_run_id: int, outcome: LayerOutcome) -> ScoreLayerResultRow:
    """Translate one pipeline LayerOutcome into its persistence row.

    Layer-specific extras (model, prompt_version) are read defensively via
    getattr, so storage never needs to import any layer service module.
    """
    layer_result = outcome.result
    details = None
    if layer_result is not None:
        details_payload = layer_result.model_dump(
            mode="json",
            exclude={
                "layer",
                "algorithm_version",
                "score",
                "explanation",
                "model",
                "prompt_version",
            },
        )
        details = json.dumps(details_payload, allow_nan=False, sort_keys=True)
    return ScoreLayerResultRow(
        score_run_id=score_run_id,
        layer=outcome.layer,
        status=outcome.status,
        algorithm_version=(
            layer_result.algorithm_version if layer_result is not None else None
        ),
        model=getattr(layer_result, "model", None),
        prompt_version=getattr(layer_result, "prompt_version", None),
        score=layer_result.score if layer_result is not None else None,
        explanation=layer_result.explanation if layer_result is not None else None,
        duration_ms=outcome.duration_ms,
        details=details,
        failure_code=outcome.failure_code,
        failure_detail=(
            outcome.failure_detail[:MAX_FAILURE_DETAIL_CHARS]
            if outcome.failure_detail
            else None
        ),
    )
