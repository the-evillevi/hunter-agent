"""Tests for the SQLModel jobs service."""

from sqlmodel import Session

from app.db.database import engine
from app.services.jobs import list_jobs


def test_list_jobs_returns_seeded_jobs() -> None:
    with Session(engine) as session:
        jobs = list_jobs(session)

    assert len(jobs) >= 2
    assert {job.title for job in jobs} >= {
        "AI/ML Engineer",
        "Senior Fullstack Engineer",
    }
