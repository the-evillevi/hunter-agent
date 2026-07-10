"""Job storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries. Keeping
database access here prevents HTML routes from becoming hard to read.
"""

from sqlmodel import Session, select

from app.models.company import Company
from app.models.job import Job, JobListItem
from app.models.location import Location
from app.models.source import Source
from app.services.blacklist import blacklist_flags


def list_jobs(session: Session, limit: int = 25) -> list[JobListItem]:
    """Return recent jobs for the home page.

    This is intentionally read-only. Later you can add functions like
    `save_scraped_job()` and `mark_job_applied()` beside it.
    """
    statement = (
        _job_list_statement()
        .order_by(Job.scraped_at.desc(), Job.id.desc())
        .limit(limit)
    )
    rows = session.exec(statement).all()
    return _overlay_blacklist_flags(session, [_item_from_row(row) for row in rows])


def get_job_list_item(session: Session, job_id: int) -> JobListItem | None:
    """Return one job's display row, for single-row fragment refreshes."""
    row = session.exec(_job_list_statement().where(Job.id == job_id)).first()
    if row is None:
        return None
    return _overlay_blacklist_flags(session, [_item_from_row(row)])[0]


def _job_list_statement():
    return (
        select(
            Job.id,
            Job.company_id,
            Job.title,
            Company.name,
            Location.name,
            Source.name,
            Job.score,
            Job.url,
        )
        .join(Company, Company.id == Job.company_id)
        .join(Location, Location.id == Job.location_id)
        .join(Source, Source.id == Job.source_id)
    )


def _item_from_row(row) -> JobListItem:
    job_id, company_id, title, company, location, source, score, url = row
    return JobListItem(
        id=job_id,
        company_id=company_id,
        title=title or "Untitled job",
        company=company,
        location=location,
        source=source,
        score=score,
        url=url,
    )


def _overlay_blacklist_flags(
    session: Session,
    items: list[JobListItem],
) -> list[JobListItem]:
    """Mark each row whose job (or its company) is blacklisted."""
    flags = blacklist_flags(session, [(item.id, item.company_id) for item in items])
    for item in items:
        flag = flags.get(item.id)
        if flag is not None and flag.blacklisted:
            item.blacklisted = True
            item.blacklist_kind = flag.kind
            item.blacklist_reason = flag.reason
    return items
