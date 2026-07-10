"""End-to-end job pipeline orchestration (HNTR-51).

This module is the one boundary that owns a full pipeline execution:
source selection, fetch/normalize, dedup/persist, and eligibility+scoring,
in that order, stopping after scoring. CV drafting, browser filling, and
submission stay explicit review actions and are never reached from here.

Both the manual dashboard trigger and the scheduler (HNTR-4) call
``run_job_pipeline``; each call persists exactly one ``pipeline_runs`` row.
Stage behavior follows the S&P ingestion pattern: individual failures are
recorded and later stages still run, so one broken source cannot stop
scoring for jobs that were already persisted.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from sqlmodel import Session, desc, select

from app.config import load_config
from app.models.config import AppConfig
from app.models.job import Job
from app.models.location import Location
from app.models.pipeline_run import PipelineRun
from app.services.ai.embeddings import OllamaEmbeddingsClient
from app.services.ai.ollama import OllamaCompletionProvider
from app.services.job_ingestion import JobIngestionSummary, ingest_normalized_jobs
from app.services.llm_scoring import LlmScoreLayer
from app.services.profiles import (
    ProfileDetail,
    get_profile,
    list_profile_runs_for_source,
)
from app.services.scoring_pipeline import (
    LAYER_WEIGHTS,
    MAX_FAILURE_DETAIL_CHARS,
    KeywordScoreLayer,
    ScoreJobInput,
    ScoreLayerRegistry,
    score_job,
)
from app.services.score_storage import save_score_run
from app.services.scraper import scrape_jobs_async
from app.services.semantic_scoring import SemanticScoreLayer
from app.services.sources import (
    JobSourceRunContext,
    NormalizedJob,
    default_source_registry,
)


PipelineTrigger = Literal["manual", "scheduled"]
PipelineStage = Literal["selection", "fetch", "ingest", "scoring", "run"]
PipelineStatus = Literal[
    "success", "partial", "failure", "skipped_overlap", "skipped_misfire"
]

# Bounded error storage keeps run rows readable without capturing
# tracebacks; anything deeper belongs in logs.
MAX_PIPELINE_ERRORS = 20


class PipelineError(BaseModel):
    """One bounded stage-level failure recorded on the run row."""

    stage: PipelineStage
    message: str


class PipelineRunSummary(BaseModel):
    """Structured result of one pipeline run, mirroring the audit row."""

    trigger_type: PipelineTrigger
    status: PipelineStatus = "success"
    started_at: datetime
    finished_at: datetime | None = None
    discovered: int = 0
    persisted: int = 0
    duplicates: int = 0
    rejected: int = 0
    scored: int = 0
    failed: int = 0
    errors: list[PipelineError] = Field(default_factory=list)
    errors_truncated: bool = False

    def add_error(self, stage: PipelineStage, message: str) -> None:
        """Record a bounded failure without letting the list grow unbounded."""
        if len(self.errors) >= MAX_PIPELINE_ERRORS:
            self.errors_truncated = True
            return
        self.errors.append(
            PipelineError(stage=stage, message=message[:MAX_FAILURE_DETAIL_CHARS])
        )

    def finish(self, finished_at: datetime) -> "PipelineRunSummary":
        """Derive the overall status once every stage has run.

        Errors alongside any progress mean ``partial`` — the decided policy
        is that ingestion failures never stop scoring of persisted jobs —
        while errors with nothing to show mean ``failure``.
        """
        self.finished_at = finished_at
        made_progress = any(
            (self.persisted, self.duplicates, self.scored, self.rejected)
        )
        if not self.errors:
            self.status = "success"
        elif made_progress:
            self.status = "partial"
        else:
            self.status = "failure"
        return self

    def response_status_code(self) -> int:
        """Map the run outcome to an HTTP status suitable for automation."""
        if self.status in ("success", "partial"):
            return 200
        if self.status in ("skipped_overlap", "skipped_misfire"):
            return 409
        return 500


@dataclass(frozen=True)
class PipelineStages:
    """Injectable stage functions so tests compose fakes.

    Each callable owns one stage boundary; the orchestrator only sequences
    them and aggregates counts, so tests never need live sources or models.
    """

    resolve_source_names: Callable[[Session], list[str]]
    list_run_contexts: Callable[[Session, str], list[JobSourceRunContext]]
    fetch_jobs: Callable[
        [Session, str, JobSourceRunContext], Awaitable[list[NormalizedJob]]
    ]
    ingest_jobs: Callable[[Session, Sequence[NormalizedJob]], JobIngestionSummary]
    score_persisted_job: Callable[[Session, int, ScoreLayerRegistry], Awaitable[str]]


def default_pipeline_stages() -> PipelineStages:
    """Wire the real service boundaries into the stage contract.

    The scoring stage carries a per-run profile cache so a run that scores
    many jobs resolves each owning profile once instead of per job.
    """
    profile_cache: dict[int, ProfileDetail] = {}

    async def score_with_cached_profiles(
        session: Session, job_id: int, registry: ScoreLayerRegistry
    ) -> str:
        return await _score_persisted_job(
            session, job_id, registry, profile_cache=profile_cache
        )

    return PipelineStages(
        resolve_source_names=_resolve_enabled_source_names,
        list_run_contexts=list_profile_runs_for_source,
        fetch_jobs=_fetch_jobs_for_source,
        ingest_jobs=ingest_normalized_jobs,
        score_persisted_job=score_with_cached_profiles,
    )


def build_scoring_registry(config: AppConfig) -> ScoreLayerRegistry:
    """Build the production scoring registry from validated configuration.

    Keyword scoring stays required; the Ollama-backed semantic and LLM
    layers are optional so a stopped local model degrades a run instead of
    failing it. One registry instance per run shares the semantic
    embedding cache across all jobs in that run.
    """
    registry = ScoreLayerRegistry()
    registry.register(
        KeywordScoreLayer(),
        weight=LAYER_WEIGHTS["keyword"],
        required=True,
    )
    base_url = str(config.ollama.base_url)
    registry.register(
        SemanticScoreLayer(OllamaEmbeddingsClient(base_url)),
        weight=LAYER_WEIGHTS["semantic"],
    )
    registry.register(
        LlmScoreLayer(OllamaCompletionProvider(config.ollama, "scorer")),
        weight=LAYER_WEIGHTS["llm"],
    )
    return registry


async def run_job_pipeline(
    session: Session,
    *,
    trigger_type: PipelineTrigger,
    config: AppConfig | None = None,
    stages: PipelineStages | None = None,
    scoring_registry: ScoreLayerRegistry | None = None,
    lock_path: Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> tuple[PipelineRun, PipelineRunSummary]:
    """Run the whole pipeline once and persist exactly one audit row.

    The lock file is checked before any stage work, so a concurrent
    trigger produces a ``skipped_overlap`` row instead of duplicate
    scraping or scoring. The pipeline stops after scoring by design.
    """
    resolved_now = now or datetime.now
    started_at = resolved_now()
    summary = PipelineRunSummary(trigger_type=trigger_type, started_at=started_at)

    # Everything after this point — even config loading and lock setup —
    # records its failure on the summary, so a run row always exists.
    lock_descriptor: int | None = None
    resolved_lock: Path | None = None
    try:
        resolved_config = config or load_config()
        resolved_stages = stages or default_pipeline_stages()
        resolved_lock = lock_path or Path(resolved_config.scheduler.lock_file)

        lock_descriptor = _acquire_lock(resolved_lock)
        if lock_descriptor is None:
            summary.status = "skipped_overlap"
            summary.finished_at = started_at
            summary.add_error(
                "run",
                f"another pipeline run holds the lock file {resolved_lock}",
            )
            return _persist_run(session, summary), summary

        if scoring_registry is None:
            scoring_registry = build_scoring_registry(resolved_config)
        await _run_stages(
            session,
            stages=resolved_stages,
            scoring_registry=scoring_registry,
            summary=summary,
        )
    except asyncio.CancelledError:
        # A cancelled request (closed browser tab) must still leave an
        # audit row: ingestion and scoring may have already committed.
        session.rollback()
        summary.add_error("run", "run was cancelled before completion")
        summary.finish(resolved_now())
        _persist_run(session, summary)
        raise
    except Exception as error:  # noqa: BLE001 - a run row must always exist
        # A mid-stage crash can leave the session in a pending-rollback
        # state; reset it so the audit row insert below cannot fail.
        session.rollback()
        summary.add_error("run", str(error))
    finally:
        if lock_descriptor is not None and resolved_lock is not None:
            _release_lock(lock_descriptor)

    summary.finish(resolved_now())
    return _persist_run(session, summary), summary


def list_recent_pipeline_runs(session: Session, limit: int = 10) -> list[PipelineRun]:
    """Return the newest runs for the dashboard panel."""
    statement = (
        select(PipelineRun)
        .order_by(desc(PipelineRun.started_at), desc(PipelineRun.id))
        .limit(limit)
    )
    return list(session.exec(statement).all())


def record_missed_pipeline_run(
    session: Session,
    *,
    scheduled_at: datetime,
    message: str,
) -> PipelineRun:
    """Persist a scheduled slot that APScheduler could not start in time."""
    summary = PipelineRunSummary(trigger_type="scheduled", started_at=scheduled_at)
    summary.status = "skipped_misfire"
    summary.finished_at = scheduled_at
    summary.add_error("run", message)
    return _persist_run(session, summary)


async def _run_stages(
    session: Session,
    *,
    stages: PipelineStages,
    scoring_registry: ScoreLayerRegistry,
    summary: PipelineRunSummary,
) -> None:
    """Sequence selection, fetch, ingest, and scoring over one session."""
    source_names = stages.resolve_source_names(session)
    if not source_names:
        summary.add_error("selection", "no job sources are enabled")
        return

    discovered: list[NormalizedJob] = []
    for source_name in source_names:
        try:
            contexts = stages.list_run_contexts(session, source_name)
        except Exception as error:  # noqa: BLE001 - keep other sources running
            session.rollback()
            summary.add_error("selection", f"{source_name}: {error}")
            continue
        for context in contexts:
            try:
                discovered.extend(
                    await stages.fetch_jobs(session, source_name, context)
                )
            except Exception as error:  # noqa: BLE001 - keep other sources running
                session.rollback()
                summary.add_error("fetch", f"{source_name}: {error}")
    summary.discovered = len(discovered)

    ingestion = stages.ingest_jobs(session, discovered)
    summary.persisted = ingestion.inserted_count
    summary.duplicates = ingestion.duplicate_count
    summary.failed += ingestion.failed_count
    for failure in ingestion.failures:
        summary.add_error(
            "ingest",
            f"{failure.source_name}: {failure.title} at {failure.company}: "
            f"{failure.reason}",
        )

    # Decided policy: ingestion failures never stop scoring. Every job that
    # made it into the database gets scored; the run just becomes partial.
    for job_id in ingestion.inserted_job_ids:
        try:
            status = await stages.score_persisted_job(session, job_id, scoring_registry)
        except Exception as error:  # noqa: BLE001 - keep scoring later jobs
            session.rollback()
            summary.failed += 1
            summary.add_error("scoring", f"job {job_id}: {error}")
            continue
        if status == "scored":
            summary.scored += 1
        elif status == "rejected":
            summary.rejected += 1
        else:
            # A "failed" score result means no layer produced a score; the
            # run must not look successful just because nothing raised.
            summary.failed += 1
            summary.add_error("scoring", f"job {job_id}: no scoring layer succeeded")


def _resolve_enabled_source_names(session: Session) -> list[str]:
    """Return the adapter names of every database-enabled job source."""
    return [
        resolved.adapter.identity.name
        for resolved in default_source_registry.resolve_enabled(session)
    ]


async def _fetch_jobs_for_source(
    session: Session,
    source_name: str,
    context: JobSourceRunContext,
) -> list[NormalizedJob]:
    """Fetch and normalize one source under one profile's run context."""
    return await scrape_jobs_async(
        source_names=[source_name],
        context=context,
        session=session,
    )


async def _score_persisted_job(
    session: Session,
    job_id: int,
    registry: ScoreLayerRegistry,
    *,
    profile_cache: dict[int, ProfileDetail] | None = None,
) -> str:
    """Score one persisted job against its owning profile and store the run.

    ``profile_cache`` lets one run resolve each profile once instead of
    re-querying the profile aggregate for every job it scores.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise ValueError(f"job {job_id} disappeared before scoring")
    location = session.get(Location, job.location_id)
    if profile_cache is not None and job.profile_id in profile_cache:
        profile = profile_cache[job.profile_id]
    else:
        profile = get_profile(session, job.profile_id)
        if profile_cache is not None:
            profile_cache[job.profile_id] = profile
    job_input = ScoreJobInput(
        title=job.title,
        description=job.description,
        location=location.name if location is not None else None,
    )
    result = await score_job(job_input, profile, registry=registry)
    save_score_run(
        session,
        job_id=job_id,
        profile_id=job.profile_id,
        result=result,
    )
    return result.status


def _acquire_lock(lock_path: Path) -> int | None:
    """Take a kernel-backed nonblocking lock on the configured file.

    ``flock`` is released automatically when a process exits, so an abandoned
    file is harmless and stale-lock reclamation cannot race a new owner.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(descriptor)
        return None
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _release_lock(descriptor: int) -> None:
    """Release the advisory lock; keep the inode for race-free reuse."""
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    os.close(descriptor)


def _persist_run(session: Session, summary: PipelineRunSummary) -> PipelineRun:
    """Insert the run's single audit row after the outcome is final."""
    errors = [error.model_dump(mode="json") for error in summary.errors]
    if summary.errors_truncated:
        errors.append({"stage": "run", "message": "additional errors truncated"})
    run = PipelineRun(
        trigger_type=summary.trigger_type,
        status=summary.status,
        started_at=summary.started_at,
        finished_at=summary.finished_at,
        discovered_count=summary.discovered,
        persisted_count=summary.persisted,
        duplicates_count=summary.duplicates,
        rejected_count=summary.rejected,
        scored_count=summary.scored,
        failed_count=summary.failed,
        errors=json.dumps(errors) if errors else None,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run
