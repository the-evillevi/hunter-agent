"""Tests for the SQLModel jobs service."""

from sqlmodel import Session

from app.services.jobs import list_jobs


def test_list_jobs_returns_fixture_jobs(
    session: Session,
    create_job,
) -> None:
    create_job(title="AI/ML Engineer")
    create_job(
        title="Senior Fullstack Engineer",
        company_name="Globant",
        location_name="Guadalajara, GDL",
        source_name="Remotive",
        score=82,
    )

    jobs = list_jobs(session)

    assert len(jobs) == 2
    assert {job.title for job in jobs} >= {
        "AI/ML Engineer",
        "Senior Fullstack Engineer",
    }
