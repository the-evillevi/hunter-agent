"""Pipeline run audit table.

This SQLModel class mirrors the pipeline_runs table in sql/hunter-agent.sql
(HNTR-51): an append-only audit trail with exactly one row per pipeline run,
so "did last night's run work?" is answerable from the dashboard. Per-job
scoring detail lives in score_runs; stage diagnostics belong in logs.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field, SQLModel


class PipelineRun(SQLModel, table=True):
    """One manual or scheduled execution of the job pipeline."""

    __tablename__ = "pipeline_runs"
    __table_args__ = (
        CheckConstraint(
            "trigger_type IN ('manual', 'scheduled')",
            name="ck_pipeline_runs_trigger_type",
        ),
        CheckConstraint(
            "status IN ('success', 'partial', 'failure', 'skipped_overlap')",
            name="ck_pipeline_runs_status",
        ),
        CheckConstraint(
            "discovered_count >= 0",
            name="ck_pipeline_runs_discovered",
        ),
        CheckConstraint(
            "persisted_count >= 0",
            name="ck_pipeline_runs_persisted",
        ),
        CheckConstraint(
            "duplicates_count >= 0",
            name="ck_pipeline_runs_duplicates",
        ),
        CheckConstraint(
            "rejected_count >= 0",
            name="ck_pipeline_runs_rejected",
        ),
        CheckConstraint(
            "scored_count >= 0",
            name="ck_pipeline_runs_scored",
        ),
        CheckConstraint(
            "failed_count >= 0",
            name="ck_pipeline_runs_failed",
        ),
        CheckConstraint(
            "errors IS NULL OR json_valid(errors)",
            name="ck_pipeline_runs_errors_json",
        ),
        Index("idx_pipeline_runs_started_at", "started_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    trigger_type: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    discovered_count: int = 0
    persisted_count: int = 0
    duplicates_count: int = 0
    rejected_count: int = 0
    scored_count: int = 0
    failed_count: int = 0
    errors: str | None = None  # JSON array of {stage, message}
    created_at: datetime = Field(default_factory=datetime.now)
