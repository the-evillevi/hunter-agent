"""Practice exercises for the applications feature.

These tests are skipped while the feature is under construction so the normal
suite stays green. Work through the APP tasks in README.md, then remove one skip
decorator at a time and use the failing assertion as your next clue.
"""

from pathlib import Path
import sqlite3

from sqlmodel import Session

from app.routes import applications as applications_route
from app.services.applications import list_applications


def test_list_applications_returns_fixture_application(
    session: Session,
    create_application,
) -> None:
    """Join applications to jobs and companies for a useful display shape."""
    create_application()

    applications = list_applications(session)

    assert len(applications) == 1

    application = applications[0]
    assert application.job_title == "AI/ML Engineer"
    assert application.company == "Kavak"
    assert application.status == "pending"
    assert application.applied_at is None


def test_applications_partial_renders_application_card(
    client, create_application
) -> None:
    """Expose the partial route and render values instead of an empty card."""
    create_application()

    response = client.get("/applications")

    assert response.status_code == 200
    assert "AI/ML Engineer" in response.text
    assert "Kavak" in response.text
    assert "pending" in response.text


def test_applications_partial_renders_empty_state(monkeypatch, client) -> None:
    """Render a useful message when there are no tracked applications."""

    def empty_applications(session: Session):
        return []

    monkeypatch.setattr(
        applications_route,
        "list_applications",
        empty_applications,
    )
    response = client.get("/applications")

    assert response.status_code == 200
    assert "No applications found yet" in response.text


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
        blacklist = connection.execute(
            """
            SELECT blacklist.reason, companies.name
            FROM blacklist
            JOIN companies ON companies.id = blacklist.company_id
            """
        ).fetchone()
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(companies)").fetchall()
        }
        history_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(removed_sp500_companies)"
            ).fetchall()
        }

    assert application == ("AI/ML Engineer", "Kavak", "pending", None)
    assert blacklist == (
        "Defense contractor — ethical concerns regarding military and surveillance contracts. Not aligned with personal values.",
        "Palantir Technologies",
    )
    assert {
        "ticker",
        "cik",
        "sector",
        "sub_industry",
        "headquarters",
        "date_added",
        "founded",
        "sp500_source",
        "sp500_source_url",
        "is_sp500",
        "sp500_weight_rank",
        "sp500_tier",
        "sp500_provider",
        "sp500_identifier",
        "sp500_sedol",
        "sp500_weight",
        "sp500_shares_held",
        "sp500_local_currency",
        "sp500_holdings_as_of",
        "sp500_last_seen_at",
        "sp500_last_updated_at",
    }.issubset(columns)
    assert {
        "company_id",
        "ticker",
        "name",
        "removal_date",
        "removal_reason",
        "source",
        "source_url",
    }.issubset(history_columns)
