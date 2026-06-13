"""Shared pytest fixtures for isolated app/database tests.

Pytest discovers ``tests/conftest.py`` automatically, which keeps the fixture
API close to the tests without adding app-only testing hooks. Each test gets a
fresh temp-file SQLite database and a rebuilt SQLModel schema. Rebuilding the
schema per test is intentionally simple to understand and maintain; the suite is
small enough that the clarity is worth more than transaction-level reuse.
"""

from collections.abc import Callable, Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import SQLModel, Session, create_engine

from app.db.database import get_session
from app.main import app
from app.models.application import Application, ApplicationStatus
from app.models.company import Company
from app.models.job import Job
from app.models.location import Location
from app.models.profile import Profile
from app.models.source import Source


@pytest.fixture()
def engine(tmp_path) -> Iterator[Engine]:
    """Return a clean SQLite engine with foreign keys enabled for one test."""
    test_engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}", echo=False)
    SQLModel.metadata.create_all(test_engine)

    try:
        yield test_engine
    finally:
        SQLModel.metadata.drop_all(test_engine)
        test_engine.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    """Return a SQLModel session bound to the isolated test engine."""
    with Session(engine) as test_session:
        yield test_session


@pytest.fixture()
def client(engine: Engine) -> Iterator[TestClient]:
    """Return a TestClient whose DB dependency points at the isolated engine.

    The app currently exposes a module-level FastAPI instance, so tests use the
    common FastAPI TDD pattern: override the dependency for the test and remove
    that override in teardown.
    """

    def override_get_session() -> Iterator[Session]:
        with Session(engine) as test_session:
            yield test_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.fixture()
def create_job(session: Session) -> Callable[..., Job]:
    """Insert only the rows a test needs for a joinable job."""

    def _create_job(
        *,
        title: str = "AI/ML Engineer",
        company_name: str = "Kavak",
        location_name: str = "CDMX",
        source_name: str = "Adzuna",
        score: int | None = 88,
        scraped_at: datetime | None = None,
    ) -> Job:
        profile = Profile(
            role_name="AI Engineer",
            salary_min=60000,
            location_type="remote",
            match_threshold=80,
            active=True,
        )
        company = Company(name=company_name)
        location = Location(name=location_name)
        source = Source(name=source_name)
        session.add_all([profile, company, location, source])
        session.commit()

        job = Job(
            profile_id=profile.id,
            title=title,
            company_id=company.id,
            location_id=location.id,
            url=f"https://example.test/jobs/{title.lower().replace(' ', '-')}",
            source_id=source.id,
            description=f"{title} role",
            hash=f"{title.lower().replace(' ', '-')}-hash",
            scraped_at=scraped_at or datetime(2026, 6, 9, 12, 0, 0),
            score=score,
            score_reasoning="Fixture score",
        )
        session.add(job)
        session.commit()
        session.refresh(job)
        return job

    return _create_job


@pytest.fixture()
def create_application(
    session: Session,
    create_job: Callable[..., Job],
) -> Callable[..., Application]:
    """Insert the minimal application graph needed by application tests."""

    def _create_application(
        *,
        job: Job | None = None,
        status: ApplicationStatus = ApplicationStatus.pending,
        applied_at: datetime | None = None,
        last_updated: datetime | None = None,
        notes: str | None = "Tailor CV to highlight PyTorch and LLM experience.",
    ) -> Application:
        application = Application(
            job_id=(job or create_job()).id,
            cv_path="/tmp/master.docx",
            status=status,
            applied_at=applied_at,
            last_updated=last_updated or datetime(2026, 6, 9, 13, 0, 0),
            notes=notes,
        )
        session.add(application)
        session.commit()
        session.refresh(application)
        return application

    return _create_application
