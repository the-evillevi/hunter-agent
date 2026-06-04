"""Application storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries.
Keeping database access here prevents HTML routes form becoming hard to read.
"""

from sqlmodel import Session, select

from app.models.application import Application, ApplicationListItem
from app.models.company import Company
from app.models.job import Job


def list_applications(session: Session, limit: int = 25) -> list[ApplicationListItem]:
    """Return recent applications for the home page.

    This is intentionally read-only.
    Later you can add functions like `save_application()`
    """
    statement = (
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
        .order_by(Application.last_updated.desc(), Application.id.desc())
        .limit(limit)
    )

    rows = session.exec(statement).all()
    return [
        ApplicationListItem(
            id=application_id,
            job_title=job_title,
            company=company,
            cv_path=cv_path,
            status=status,
            applied_at=applied_at,
            last_updated=last_updated,
            notes=notes,
        )
        for (
            application_id,
            job_title,
            company,
            cv_path,
            status,
            applied_at,
            last_updated,
            notes,
        ) in rows
    ]
