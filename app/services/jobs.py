"""Job storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries. Keeping
database access here prevents HTML routes from becoming hard to read.
"""

from sqlmodel import Session, select

from app.models.company import Company
from app.models.job import Job, JobListItem
from app.models.location import Location
from app.models.source import Source


def list_jobs(session: Session, limit: int = 25) -> list[JobListItem]:
    """Return recent jobs for the home page.

    This is intentionally read-only. Later you can add functions like
    `save_scraped_job()` and `mark_job_applied()` beside it.
    """
    statement = (
        select(
            Job.id,
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
        .order_by(Job.scraped_at.desc(), Job.id.desc())
        .limit(limit)
    )

    rows = session.exec(statement).all()
    return [
        JobListItem(
            id=job_id,
            title=title or "Untitled job",
            company=company,
            location=location,
            source=source,
            score=score,
            url=url,
        )
        for job_id, title, company, location, source, score, url in rows
    ]
