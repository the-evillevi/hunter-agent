"""Score-run history tables.

These SQLModel classes mirror the score_runs and score_layer_results
tables in sql/hunter-agent.sql (HNTR-10): an append-only audit trail of
every scoring pipeline run, so scores can be explained, compared across
algorithm/model/prompt versions, and recalibrated later. The jobs table's
score/score_reasoning columns remain only a latest-result cache.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, Index
from sqlmodel import Field, SQLModel


class ScoreRun(SQLModel, table=True):
    """One pipeline run of one job against one profile."""

    __tablename__ = "score_runs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('scored', 'rejected', 'failed')",
            name="ck_score_runs_status",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_score_runs_score",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_score_runs_duration",
        ),
        CheckConstraint(
            "eligibility_reasons IS NULL OR json_valid(eligibility_reasons)",
            name="ck_score_runs_reasons_json",
        ),
        CheckConstraint(
            "unknowns IS NULL OR json_valid(unknowns)",
            name="ck_score_runs_unknowns_json",
        ),
        CheckConstraint(
            "warnings IS NULL OR json_valid(warnings)",
            name="ck_score_runs_warnings_json",
        ),
        Index("idx_score_runs_job_profile", "job_id", "profile_id", "created_at"),
    )

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id")
    profile_id: int = Field(foreign_key="profiles.id")
    pipeline_version: str
    weights_version: str
    status: str
    score: int | None = None
    explanation: str | None = None
    eligibility_reasons: str | None = None  # JSON array of {code, detail}
    unknowns: str | None = None  # JSON array of unchecked constraint names
    warnings: str | None = None  # JSON array of human-readable warnings
    duration_ms: int | None = None
    created_at: datetime = Field(default_factory=datetime.now)


class ScoreLayerResultRow(SQLModel, table=True):
    """One layer outcome inside one score run."""

    __tablename__ = "score_layer_results"
    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'skip', 'failure')",
            name="ck_score_layer_results_status",
        ),
        CheckConstraint(
            "score IS NULL OR score BETWEEN 0 AND 100",
            name="ck_score_layer_results_score",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_score_layer_results_duration",
        ),
        CheckConstraint(
            "details IS NULL OR json_valid(details)",
            name="ck_score_layer_results_details_json",
        ),
        CheckConstraint(
            "failure_detail IS NULL OR length(failure_detail) <= 500",
            name="ck_score_layer_results_failure_detail",
        ),
        Index("idx_score_layer_results_run", "score_run_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    score_run_id: int = Field(foreign_key="score_runs.id")
    layer: str
    status: str
    algorithm_version: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    score: int | None = None
    explanation: str | None = None
    duration_ms: int | None = None
    details: str | None = None  # JSON object of layer-specific result fields
    failure_code: str | None = None
    failure_detail: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
