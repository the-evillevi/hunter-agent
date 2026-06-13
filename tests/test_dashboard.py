"""Tests for dashboard summary metrics."""

from sqlmodel import Session

from app.models.application import ApplicationStatus
from app.services.dashboard import get_dashboard_metrics


def test_get_dashboard_metrics_returns_fixture_counts(
    session: Session,
    create_job,
    create_application,
) -> None:
    job = create_job(title="AI/ML Engineer", score=88)
    create_job(
        title="Senior Fullstack Engineer",
        company_name="Globant",
        location_name="Guadalajara, GDL",
        source_name="Remotive",
        score=82,
    )
    create_application(job=job, applied_at=None, status=ApplicationStatus.pending)

    metrics = get_dashboard_metrics(session)

    assert metrics.tracked_jobs == 2
    assert metrics.total_applications == 1
    assert metrics.applied_jobs == 0
    assert metrics.average_score == 85
    assert metrics.status_counts[ApplicationStatus.pending] == 1
    assert set(metrics.status_counts) == set(ApplicationStatus)
