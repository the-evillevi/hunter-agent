"""Tests for dashboard summary metrics."""

from sqlmodel import Session

from app.db.database import engine
from app.models.application import ApplicationStatus
from app.services.dashboard import get_dashboard_metrics


def test_get_dashboard_metrics_returns_seeded_counts() -> None:
    with Session(engine) as session:
        metrics = get_dashboard_metrics(session)

    assert metrics.tracked_jobs >= 2
    assert metrics.total_applications >= 1
    assert metrics.applied_jobs == 0
    assert metrics.average_score == 85
    assert metrics.status_counts[ApplicationStatus.pending] >= 1
    assert set(metrics.status_counts) == set(ApplicationStatus)
