"""Dashboard summary metrics.

These read-only helpers keep aggregate database queries out of route handlers.
"""

from sqlalchemy import func
from sqlmodel import Session, select

from app.models.application import Application, ApplicationStatus
from app.models.dashboard import DashboardMetrics
from app.models.job import Job


def get_dashboard_metrics(session: Session) -> DashboardMetrics:
    """Return whole-database metrics for the home dashboard."""
    tracked_jobs = session.exec(select(func.count(Job.id))).one()
    total_applications = session.exec(select(func.count(Application.id))).one()
    applied_jobs = session.exec(
        select(func.count(Application.id)).where(Application.applied_at.is_not(None))
    ).one()
    average_score = session.exec(select(func.avg(Job.score))).one()

    status_counts = {status: 0 for status in ApplicationStatus}
    rows = session.exec(
        select(Application.status, func.count(Application.id)).group_by(
            Application.status
        )
    ).all()
    for status, count in rows:
        status_counts[ApplicationStatus(status)] = count

    return DashboardMetrics(
        tracked_jobs=tracked_jobs,
        total_applications=total_applications,
        applied_jobs=applied_jobs,
        average_score=round(average_score) if average_score is not None else None,
        status_counts=status_counts,
    )
