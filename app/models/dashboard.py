"""Display models for dashboard summary metrics."""

from sqlmodel import SQLModel

from app.models.application import ApplicationStatus


class DashboardMetrics(SQLModel):
    """Counts and score summaries rendered on the home dashboard."""

    tracked_jobs: int
    total_applications: int
    applied_jobs: int
    average_score: int | None
    status_counts: dict[ApplicationStatus, int]
