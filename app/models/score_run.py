"""Score-run history tables.

These SQLModel classes mirror the score_runs and score_layer_results
tables in sql/hunter-agent.sql (HNTR-10): an append-only audit trail of
every scoring pipeline run, so scores can be explained, compared across
algorithm/model/prompt versions, and recalibrated later. The jobs table's
score/score_reasoning columns remain only a latest-result cache.
"""

from datetime import datetime

from sqlmodel import Field, SQLModel


class ScoreRun(SQLModel, table=True):
    """One pipeline run of one job against one profile."""

    __tablename__ = "score_runs"

    id: int | None = Field(default=None, primary_key=True)
    job_id: int = Field(foreign_key="jobs.id", index=True)
    profile_id: int = Field(foreign_key="profiles.id")
    pipeline_version: str
    weights_version: str
    status: str  # scored | rejected | failed (CHECK lives in sql/)
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

    id: int | None = Field(default=None, primary_key=True)
    score_run_id: int = Field(foreign_key="score_runs.id", index=True)
    layer: str
    status: str  # success | skip | failure (CHECK lives in sql/)
    algorithm_version: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    score: int | None = None
    explanation: str | None = None
    duration_ms: int | None = None
    failure_code: str | None = None
    failure_detail: str | None = None
    created_at: datetime = Field(default_factory=datetime.now)
