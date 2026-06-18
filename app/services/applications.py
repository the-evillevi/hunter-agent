"""Application storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries.
Keeping database access here prevents HTML routes form becoming hard to read.
"""

from datetime import datetime

from sqlmodel import Session, select

from app.models.application import Application, ApplicationListItem, ApplicationStatus
from app.models.company import Company
from app.models.job import Job


APPLICATION_STATUS_ORDER = [
    ApplicationStatus.pending,
    ApplicationStatus.draft,
    ApplicationStatus.applied,
    ApplicationStatus.rejected,
    ApplicationStatus.acknowledged,
    ApplicationStatus.interviews,
    ApplicationStatus.offer,
    ApplicationStatus.accepted,
    ApplicationStatus.ghosted,
]


def list_applications(session: Session, limit: int = 25) -> list[ApplicationListItem]:
    """Return recent applications for the home page.

    This is intentionally read-only.
    Later you can add functions like `save_application()`
    """
    statement = (
        _application_list_statement()
        .order_by(Application.last_updated.desc(), Application.id.desc())
        .limit(limit)
    )

    rows = session.exec(statement).all()
    return [_application_list_item_from_row(row) for row in rows]


def get_application_list_item(
    session: Session,
    application_id: int,
) -> ApplicationListItem | None:
    """Return the joined display row for one application."""
    statement = _application_list_statement().where(Application.id == application_id)
    row = session.exec(statement).first()
    if row is None:
        return None
    return _application_list_item_from_row(row)


def update_application_status(
    session: Session,
    *,
    application_id: int,
    status: ApplicationStatus,
) -> ApplicationListItem:
    """Persist a status change and return the refreshed display row."""
    application = session.get(Application, application_id)
    if application is None:
        raise LookupError(f"Application {application_id} was not found")

    now = datetime.now()
    application.status = status
    application.last_updated = now
    if status == ApplicationStatus.applied and application.applied_at is None:
        application.applied_at = now

    session.add(application)
    session.commit()

    updated = get_application_list_item(session, application_id)
    if updated is None:
        raise LookupError(f"Application {application_id} was not found")
    return updated


def _application_list_statement():
    return (
        select(
            Application.id,
            Job.title,
            Company.name,
            Application.cv_path,
            Application.status,
            Application.applied_at,
            Application.last_updated,
            Application.notes,
        )
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
    )


def _application_list_item_from_row(row) -> ApplicationListItem:
    (
        application_id,
        job_title,
        company,
        cv_path,
        status,
        applied_at,
        last_updated,
        notes,
    ) = row
    return ApplicationListItem(
        id=application_id,
        job_title=job_title,
        company=company,
        cv_path=cv_path,
        status=status,
        applied_at=applied_at,
        last_updated=last_updated,
        notes=notes,
    )
