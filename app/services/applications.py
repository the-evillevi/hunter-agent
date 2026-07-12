"""Application storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries.
Keeping database access here prevents HTML routes form becoming hard to read.
"""

from datetime import datetime

from sqlalchemy import and_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.models.application import Application, ApplicationListItem, ApplicationStatus
from app.models.company import Company
from app.models.job import Job
from app.models.resume import ResumeProfile, ResumeTailorRun
from app.services.blacklist import (
    BlacklistedJobError,
    blacklist_flags,
    is_job_blacklisted,
)


class DuplicateApplicationError(ValueError):
    """The job already has an application (job_id is unique)."""


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
    items = [_application_list_item_from_row(row) for row in rows]
    return _overlay_blacklist_flags(session, items)


def get_application_list_item(
    session: Session,
    application_id: int,
) -> ApplicationListItem | None:
    """Return the joined display row for one application."""
    statement = _application_list_statement().where(Application.id == application_id)
    row = session.exec(statement).first()
    if row is None:
        return None
    item = _application_list_item_from_row(row)
    return _overlay_blacklist_flags(session, [item])[0]


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


def update_application_notes(
    session: Session,
    *,
    application_id: int,
    notes: str | None,
) -> ApplicationListItem:
    """Persist an explicit notes save; an empty submission clears them."""
    application = session.get(Application, application_id)
    if application is None:
        raise LookupError(f"Application {application_id} was not found")

    cleaned = (notes or "").strip() or None
    application.notes = cleaned
    application.last_updated = datetime.now()
    session.add(application)
    session.commit()

    updated = get_application_list_item(session, application_id)
    if updated is None:  # pragma: no cover - the row was just updated
        raise LookupError(f"Application {application_id} was not found")
    return updated


def create_application_draft(session: Session, *, job_id: int) -> Application:
    """The one sanctioned way to promote a job into an application draft.

    Guards live here in the service — not in a route — so every entry
    point (the HNTR-2 review queue, any future API) inherits them:
    the job must exist, must not be blacklisted (directly or via its
    company), and must not already have an application.
    """
    if session.get(Job, job_id) is None:
        raise LookupError(f"Job {job_id} was not found")
    if is_job_blacklisted(session, job_id):
        raise BlacklistedJobError(
            f"job {job_id} is blacklisted and cannot become an application"
        )
    existing = session.exec(
        select(Application).where(Application.job_id == job_id)
    ).first()
    if existing is not None:
        raise DuplicateApplicationError(
            f"job {job_id} already has application {existing.id}"
        )

    draft = Application(job_id=job_id, status=ApplicationStatus.draft)
    session.add(draft)
    try:
        session.commit()
    except IntegrityError as error:
        # The unique job_id can still trip between the check and the
        # insert (double-click); report it as the same duplicate error.
        session.rollback()
        raise DuplicateApplicationError(
            f"job {job_id} already has an application"
        ) from error
    session.refresh(draft)
    return draft


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
            Application.job_id,
            Job.company_id,
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
        job_id,
        company_id,
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
        job_id=job_id,
        company_id=company_id,
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


def _overlay_blacklist_flags(
    session: Session,
    items: list[ApplicationListItem],
) -> list[ApplicationListItem]:
    """Mark each card whose job (or its company) is blacklisted."""
    flags = blacklist_flags(session, [(item.job_id, item.company_id) for item in items])
    for item in items:
        flag = flags.get(item.job_id)
        if flag is not None and flag.blacklisted:
            item.blacklisted = True
            item.blacklist_kind = flag.kind
            item.blacklist_reason = flag.reason
    return items
