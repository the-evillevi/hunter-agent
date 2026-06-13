"""Acceptance tests for shared pytest fixtures."""

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.db.database import get_session
from app.main import app
from app.models.company import Company
from app.models.job import Job


def test_isolated_session_enforces_foreign_keys(session: Session) -> None:
    """Fixture databases should behave like app SQLite databases."""
    session.add(
        Job(
            profile_id=999,
            title="Broken fixture job",
            company_id=999,
            location_id=999,
            source_id=999,
            scraped_at=datetime(2026, 6, 9),
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_isolated_session_starts_empty(session: Session) -> None:
    """Each test receives a rebuilt schema without data from previous tests."""
    assert session.exec(select(Company)).all() == []


def test_test_client_uses_and_clears_session_override(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert get_session in app.dependency_overrides


def test_test_client_override_is_cleared_after_previous_test() -> None:
    assert get_session not in app.dependency_overrides
