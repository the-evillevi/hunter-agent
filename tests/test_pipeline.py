"""Service tests for the end-to-end pipeline orchestrator (HNTR-51).

Every test composes fake stage callables through ``PipelineStages`` so no
live source, model, or browser automation is ever touched. The assertions
focus on the audit contract: exactly one ``pipeline_runs`` row per run,
correct status derivation, and bounded error text.
"""

import asyncio
import fcntl
import json
import os
import subprocess
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, select

from app.models.pipeline_run import PipelineRun
from app.services.job_ingestion import JobIngestionFailure, JobIngestionSummary
from app.services.pipeline import (
    MAX_PIPELINE_ERRORS,
    PipelineRunSummary,
    PipelineStages,
    list_recent_pipeline_runs,
    run_job_pipeline,
)
from app.services.sources import JobSourceRunContext


def make_stages(
    *,
    source_names: list[str] | None = None,
    contexts: list[JobSourceRunContext] | None = None,
    fetched: list | None = None,
    ingestion: JobIngestionSummary | None = None,
    score_statuses: dict[int, str] | None = None,
    calls: list[str] | None = None,
) -> PipelineStages:
    """Build fake stages that record calls and return canned results."""
    recorded = calls if calls is not None else []
    resolved_names = source_names if source_names is not None else ["fake-source"]
    resolved_contexts = (
        contexts if contexts is not None else [JobSourceRunContext(profile_id=1)]
    )
    resolved_ingestion = ingestion if ingestion is not None else JobIngestionSummary()

    def resolve_source_names(session: Session) -> list[str]:
        recorded.append("resolve")
        return resolved_names

    def list_run_contexts(
        session: Session, source_name: str
    ) -> list[JobSourceRunContext]:
        recorded.append(f"contexts:{source_name}")
        return resolved_contexts

    async def fetch_jobs(
        session: Session, source_name: str, context: JobSourceRunContext
    ) -> list:
        recorded.append(f"fetch:{source_name}")
        return list(fetched or [])

    def ingest_jobs(session: Session, jobs) -> JobIngestionSummary:
        recorded.append("ingest")
        return resolved_ingestion

    async def score_persisted_job(session: Session, job_id: int, registry) -> str:
        recorded.append(f"score:{job_id}")
        status = (score_statuses or {}).get(job_id, "scored")
        if status == "raise":
            raise RuntimeError(f"model exploded for job {job_id}")
        return status

    return PipelineStages(
        resolve_source_names=resolve_source_names,
        list_run_contexts=list_run_contexts,
        fetch_jobs=fetch_jobs,
        ingest_jobs=ingest_jobs,
        score_persisted_job=score_persisted_job,
    )


def run_pipeline(session: Session, tmp_path: Path, stages: PipelineStages, **kwargs):
    """Run the orchestrator with a test-owned lock path."""
    return asyncio.run(
        run_job_pipeline(
            session,
            trigger_type=kwargs.pop("trigger_type", "manual"),
            stages=stages,
            scoring_registry=kwargs.pop("scoring_registry", object()),
            lock_path=kwargs.pop("lock_path", tmp_path / "pipeline.lock"),
            **kwargs,
        )
    )


def all_runs(session: Session) -> list[PipelineRun]:
    return list(session.exec(select(PipelineRun)).all())


def test_successful_run_persists_exactly_one_success_row(session, tmp_path) -> None:
    ingestion = JobIngestionSummary(inserted_job_ids=[11, 12], duplicate_count=3)
    stages = make_stages(fetched=["a", "b"], ingestion=ingestion)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "success"
    assert (summary.discovered, summary.persisted, summary.scored) == (2, 2, 2)
    rows = all_runs(session)
    assert len(rows) == 1
    assert rows[0].id == run.id
    assert rows[0].status == "success"
    assert rows[0].trigger_type == "manual"
    assert rows[0].discovered_count == 2
    assert rows[0].duplicates_count == 3
    assert rows[0].scored_count == 2
    assert rows[0].errors is None
    assert rows[0].finished_at is not None


def test_fetch_failure_still_ingests_other_sources_and_is_partial(
    session, tmp_path
) -> None:
    calls: list[str] = []
    ingestion = JobIngestionSummary(inserted_job_ids=[21])

    stages = make_stages(
        source_names=["broken", "working"], ingestion=ingestion, calls=calls
    )

    async def fetch_jobs(session_, source_name, context):
        calls.append(f"fetch:{source_name}")
        if source_name == "broken":
            raise ConnectionError("source API is down")
        return ["job"]

    stages = replace(stages, fetch_jobs=fetch_jobs)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "partial"
    assert "fetch:working" in calls
    assert "score:21" in calls
    assert any(error.stage == "fetch" for error in summary.errors)
    assert all_runs(session)[0].status == "partial"


def test_ingest_row_failures_never_stop_scoring(session, tmp_path) -> None:
    ingestion = JobIngestionSummary(
        inserted_job_ids=[31, 32],
        duplicate_count=1,
        failures=[
            JobIngestionFailure(
                title="Bad row",
                company="Acme",
                source_name="fake-source",
                reason="record has an empty location",
            )
        ],
    )
    calls: list[str] = []
    stages = make_stages(fetched=["a"], ingestion=ingestion, calls=calls)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "partial"
    assert summary.scored == 2
    assert summary.failed == 1
    assert {"score:31", "score:32"} <= set(calls)
    assert any(error.stage == "ingest" for error in summary.errors)


def test_scoring_exception_counts_failed_and_continues(session, tmp_path) -> None:
    ingestion = JobIngestionSummary(inserted_job_ids=[41, 42, 43])
    stages = make_stages(
        fetched=["a"],
        ingestion=ingestion,
        score_statuses={41: "scored", 42: "raise", 43: "rejected"},
    )

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "partial"
    assert (summary.scored, summary.rejected, summary.failed) == (1, 1, 1)
    assert any(
        error.stage == "scoring" and "42" in error.message for error in summary.errors
    )


def test_held_lock_records_one_skipped_overlap_row_without_stage_work(
    session, tmp_path
) -> None:
    lock_path = tmp_path / "pipeline.lock"
    held_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(held_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    calls: list[str] = []
    stages = make_stages(calls=calls)

    try:
        run, summary = run_pipeline(session, tmp_path, stages, lock_path=lock_path)
    finally:
        fcntl.flock(held_descriptor, fcntl.LOCK_UN)
        os.close(held_descriptor)

    assert summary.status == "skipped_overlap"
    assert calls == []
    rows = all_runs(session)
    assert len(rows) == 1
    assert rows[0].status == "skipped_overlap"
    # The pre-existing lock file must be left alone for its real owner.
    assert lock_path.exists()


def test_abandoned_lock_file_is_reused(session, tmp_path) -> None:
    lock_path = tmp_path / "pipeline.lock"
    # A finished process leaves only file contents; its kernel lock is gone.
    dead = subprocess.Popen(["true"])
    dead.wait()
    lock_path.write_text(str(dead.pid))

    run, summary = run_pipeline(session, tmp_path, make_stages(), lock_path=lock_path)

    assert summary.status == "success"
    assert lock_path.exists()


def test_failed_score_results_never_report_success(session, tmp_path) -> None:
    ingestion = JobIngestionSummary(inserted_job_ids=[51, 52])
    stages = make_stages(
        fetched=["a"],
        ingestion=ingestion,
        score_statuses={51: "failed", 52: "failed"},
    )

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "partial"
    assert summary.failed == 2
    assert any(
        error.stage == "scoring" and "no scoring layer succeeded" in error.message
        for error in summary.errors
    )


def test_lock_is_released_after_a_run(session, tmp_path) -> None:
    lock_path = tmp_path / "pipeline.lock"
    stages = make_stages()

    run_pipeline(session, tmp_path, stages, lock_path=lock_path)

    assert lock_path.exists()
    run, summary = run_pipeline(session, tmp_path, stages, lock_path=lock_path)
    assert summary.status != "skipped_overlap"


def test_scoring_database_failure_rolls_back_and_audit_row_survives(
    session, tmp_path
) -> None:
    ingestion = JobIngestionSummary(inserted_job_ids=[61, 62])
    stages = make_stages(fetched=["a"], ingestion=ingestion)

    async def score_with_failed_transaction(session_, job_id, registry) -> str:
        if job_id == 61:
            session_.add(
                PipelineRun(
                    trigger_type="invalid",
                    status="success",
                    started_at=datetime.now(),
                )
            )
            session_.flush()
        return "scored"

    stages = replace(stages, score_persisted_job=score_with_failed_transaction)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "partial"
    assert (summary.scored, summary.failed) == (1, 1)
    assert run.id is not None
    assert len(all_runs(session)) == 1


def test_no_enabled_sources_is_a_visible_failure(session, tmp_path) -> None:
    stages = make_stages(source_names=[])

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "failure"
    assert any(error.stage == "selection" for error in summary.errors)
    assert all_runs(session)[0].status == "failure"


def test_scheduled_trigger_type_is_persisted(session, tmp_path) -> None:
    run, summary = run_pipeline(
        session, tmp_path, make_stages(), trigger_type="scheduled"
    )

    assert all_runs(session)[0].trigger_type == "scheduled"


def test_error_text_is_bounded(session, tmp_path) -> None:
    failures = [
        JobIngestionFailure(
            title=f"Job {index}",
            company="Acme",
            source_name="fake-source",
            reason="x" * 2000,
        )
        for index in range(MAX_PIPELINE_ERRORS + 10)
    ]
    ingestion = JobIngestionSummary(failures=failures)
    stages = make_stages(fetched=["a"], ingestion=ingestion)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert len(summary.errors) == MAX_PIPELINE_ERRORS
    assert all(len(error.message) <= 500 for error in summary.errors)
    stored_errors = json.loads(all_runs(session)[0].errors)
    assert stored_errors[-1]["message"] == "additional errors truncated"


def test_summary_finish_status_matrix() -> None:
    from datetime import datetime

    def summary_with(**kwargs) -> PipelineRunSummary:
        base = PipelineRunSummary(trigger_type="manual", started_at=datetime.now())
        for key, value in kwargs.items():
            setattr(base, key, value)
        return base

    clean = summary_with(scored=3)
    assert clean.finish(datetime.now()).status == "success"

    progressed = summary_with(persisted=1)
    progressed.add_error("fetch", "one source broke")
    assert progressed.finish(datetime.now()).status == "partial"

    stuck = summary_with()
    stuck.add_error("selection", "nothing enabled")
    assert stuck.finish(datetime.now()).status == "failure"


def test_list_recent_pipeline_runs_orders_newest_first(session, tmp_path) -> None:
    stages = make_stages()
    for _ in range(3):
        run_pipeline(session, tmp_path, stages)

    runs = list_recent_pipeline_runs(session, limit=2)

    assert len(runs) == 2
    assert runs[0].id > runs[1].id


def test_run_row_survives_unexpected_stage_crash(session, tmp_path) -> None:
    def resolve_source_names(session_) -> list[str]:
        raise RuntimeError("registry blew up")

    stages = replace(make_stages(), resolve_source_names=resolve_source_names)

    run, summary = run_pipeline(session, tmp_path, stages)

    assert summary.status == "failure"
    assert any(error.stage == "run" for error in summary.errors)
    assert len(all_runs(session)) == 1
