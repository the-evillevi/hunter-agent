"""Application storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries.
Keeping database access here prevents HTML routes form becoming hard to read.
"""

from datetime import datetime

from sqlalchemy import and_
from sqlmodel import Session, func, select

from app.models.application import Application, ApplicationListItem, ApplicationStatus
from app.models.company import Company
from app.models.job import Job
from app.models.resume import ResumeProfile, ResumeTailorRun


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
    # Newest tailoring run per job, so each card can link the resume
    # variant that was actually produced for its job (None if never
    # tailored, or if that variant was soft-deleted).
    latest_runs = (
        select(
            ResumeTailorRun.job_id.label("job_id"),
            func.max(ResumeTailorRun.id).label("run_id"),
        )
        .group_by(ResumeTailorRun.job_id)
        .subquery()
    )

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
            ResumeProfile.id,
            ResumeProfile.name,
        )
        .join(Job, Job.id == Application.job_id)
        .join(Company, Company.id == Job.company_id)
        .join(latest_runs, latest_runs.c.job_id == Application.job_id, isouter=True)
        .join(
            ResumeTailorRun,
            ResumeTailorRun.id == latest_runs.c.run_id,
            isouter=True,
        )
        .join(
            ResumeProfile,
            and_(
                ResumeProfile.id == ResumeTailorRun.output_profile_id,
                ResumeProfile.deleted_at.is_(None),
            ),
            isouter=True,
        )
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
        resume_id,
        resume_name,
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
        resume_id=resume_id,
        resume_name=resume_name,
    )
