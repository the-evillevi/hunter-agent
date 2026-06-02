"""Practice exercises for the applications feature.

These tests are skipped while the feature is under construction so the normal
suite stays green. Work through the APP tasks in README.md, then remove one skip
decorator at a time and use the failing assertion as your next clue.
"""

from pathlib import Path
import sqlite3

from fastapi.testclient import TestClient
import pytest
from sqlmodel import Session

from app.db.database import engine
from app.main import app
from app.services.applications import list_applications


@pytest.mark.skip(reason="APP-05: implement the SQLModel applications list query")
def test_list_applications_returns_seeded_application() -> None:
    """Join applications to jobs and companies for a useful display shape."""
    with Session(engine) as session:
        applications = list_applications(session)

    assert len(applications) >= 1

    application = applications[0]
    assert application.title == "AI/ML Engineer"
    assert application.company == "Kavak"
    assert application.status == "pending"
    assert application.applied_at is None


@pytest.mark.skip(reason="APP-06 and APP-07: register and render applications")
def test_applications_partial_renders_application_card() -> None:
    """Expose the partial route and render values instead of an empty card."""
    client = TestClient(app)

    response = client.get("/applications")

    assert response.status_code == 200
    assert "AI/ML Engineer" in response.text
    assert "Kavak" in response.text
    assert "pending" in response.text


@pytest.mark.skip(reason="APP-01, APP-02, and APP-10: make schema replay reliable")
def test_schema_and_seed_scripts_rebuild_database(tmp_path: Path) -> None:
    """A clean SQLite database should be reproducible from committed SQL."""
    schema_path = Path("sql/hunter-agent.sql")
    seed_path = Path("sql/seed.sql")
    database_path = tmp_path / "hunter-agent.db"

    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(schema_path.read_text())
        connection.executescript(seed_path.read_text())

        application = connection.execute(
            """
            SELECT jobs.title, companies.name, applications.status,
                   applications.applied_at
            FROM applications
            JOIN jobs ON jobs.id = applications.job_id
            JOIN companies ON companies.id = jobs.company_id
            """
        ).fetchone()

    assert application == ("AI/ML Engineer", "Kavak", "pending", None)
