"""Job storage and lookup helpers.

This file is the bridge between route handlers and SQLite queries. Keeping SQL
here prevents HTML routes from becoming hard to read as the app grows.
"""

from sqlite3 import Connection

from app.models.job import Job


def list_jobs(connection: Connection, limit: int = 25) -> list[Job]:
    """Return recent jobs for the home page.

    This is intentionally read-only. Later you can add functions like
    `save_scraped_job()` and `mark_job_applied()` beside it.
    """
    cursor = connection.execute(
        """
        SELECT
            jobs.id,
            jobs.title,
            companies.name AS company,
            locations.name AS location,
            sources.name AS source,
            jobs.score,
            jobs.url
        FROM jobs
        JOIN companies ON companies.id = jobs.company_id
        JOIN locations ON locations.id = jobs.location_id
        JOIN sources ON sources.id = jobs.source_id
        ORDER BY jobs.scraped_at DESC, jobs.id DESC
        LIMIT ?
        """,
        (limit,),
    )

    return [
        Job(
            id=row["id"],
            title=row["title"] or "Untitled job",
            company=row["company"],
            location=row["location"],
            source=row["source"],
            score=row["score"],
            url=row["url"],
        )
        for row in cursor.fetchall()
    ]
