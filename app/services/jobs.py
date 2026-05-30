"""Job storage and lookup helpers.

This file is the bridge between route handlers and SQLModel queries. Keeping
database access here prevents HTML routes from becoming hard to read.
"""

from sqlmodel import Session, select

from app.models.job import Company, JobListItem, JobRecord, Location, Source


def list_jobs(session: Session, limit: int = 25) -> list[JobListItem]:
    """Return recent jobs for the home page.

    This is intentionally read-only. Later you can add functions like
    `save_scraped_job()` and `mark_job_applied()` beside it.
    """
    statement = (
        select(
            JobRecord.id,
            JobRecord.title,
            Company.name,
            Location.name,
            Source.name,
            JobRecord.score,
            JobRecord.url,
        )
        .join(Company, Company.id == JobRecord.company_id)
        .join(Location, Location.id == JobRecord.location_id)
        .join(Source, Source.id == JobRecord.source_id)
        .order_by(JobRecord.scraped_at.desc(), JobRecord.id.desc())
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
