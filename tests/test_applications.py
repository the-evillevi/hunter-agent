"""Practice exercises for the applications feature.

These tests are skipped while the feature is under construction so the normal
suite stays green. Work through the APP tasks in README.md, then remove one skip
decorator at a time and use the failing assertion as your next clue.
"""

from pathlib import Path
import sqlite3

from sqlmodel import Session

from app.models.application import ApplicationStatus
from app.routes import applications as applications_route
from app.services.applications import list_applications, update_application_status


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


def test_update_application_status_moves_across_statuses(
    session: Session,
    create_application,
) -> None:
    """Status edits are flexible and refresh the display row."""
    application = create_application(status=ApplicationStatus.rejected)
    previous_last_updated = application.last_updated

    updated = update_application_status(
        session,
        application_id=application.id,
        status=ApplicationStatus.draft,
    )

    assert updated.status == ApplicationStatus.draft
    assert updated.applied_at is None
    assert updated.last_updated > previous_last_updated


def test_update_application_status_sets_applied_at_only_when_missing(
    session: Session,
    create_application,
) -> None:
    """Moving into applied records first application time without losing history."""
    application = create_application(status=ApplicationStatus.pending)

    applied = update_application_status(
        session,
        application_id=application.id,
        status=ApplicationStatus.applied,
    )
    applied_at = applied.applied_at

    assert applied_at is not None

    rejected = update_application_status(
        session,
        application_id=application.id,
        status=ApplicationStatus.rejected,
    )
    assert rejected.applied_at == applied_at

    reapplied = update_application_status(
        session,
        application_id=application.id,
        status=ApplicationStatus.applied,
    )
    assert reapplied.applied_at == applied_at


def test_update_application_status_missing_application_raises(
    session: Session,
) -> None:
    """A missing application ID is reported distinctly from bad status input."""
    try:
        update_application_status(
            session,
            application_id=999,
            status=ApplicationStatus.applied,
        )
    except LookupError as error:
        assert "Application 999" in str(error)
    else:
        raise AssertionError("Expected missing application to raise LookupError")


def test_applications_partial_renders_application_card(
    client, create_application
) -> None:
    """Expose the partial route and render values instead of an empty card."""
    create_application()

    response = client.get("/applications/partials/list")

    assert response.status_code == 200
    assert "AI/ML Engineer" in response.text
    assert "Kavak" in response.text
    assert "pending" in response.text


def test_application_status_patch_renders_only_updated_card(
    client,
    create_application,
    create_job,
) -> None:
    """HTMX receives one fresh card for the edited application."""
    first_application = create_application()
    create_application(
        job=create_job(
            title="Backend Engineer",
            company_name="Globant",
            location_name="Guadalajara",
            source_name="Remotive",
        ),
        status=ApplicationStatus.pending,
        notes="A second card that should not be returned.",
    )

    response = client.patch(
        f"/applications/{first_application.id}",
        data={"status": "applied"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.text.count("<article") == 1
    assert f"APP-{first_application.id}: AI/ML Engineer" in response.text
    assert "applied" in response.text
    assert "A second card that should not be returned." not in response.text


def test_application_status_patch_rejects_invalid_status(
    client,
    create_application,
) -> None:
    """Unknown target statuses are client errors."""
    application = create_application()

    response = client.patch(
        f"/applications/{application.id}",
        data={"status": "sent-to-space"},
    )

    assert response.status_code == 400


def test_application_status_patch_returns_404_for_missing_application(client) -> None:
    """Missing application IDs return a proper not found response."""
    response = client.patch("/applications/999", data={"status": "applied"})

    assert response.status_code == 404


def test_applications_partial_exposes_all_status_controls(
    client,
    create_application,
) -> None:
    """Status controls are available immediately in the requested order."""
    create_application(status=ApplicationStatus.pending)

    response = client.get("/applications/partials/list")

    assert response.status_code == 200
    positions = [
        response.text.index(f'value="{status.value}"')
        for status in (
            ApplicationStatus.pending,
            ApplicationStatus.draft,
            ApplicationStatus.applied,
            ApplicationStatus.rejected,
            ApplicationStatus.acknowledged,
            ApplicationStatus.interviews,
            ApplicationStatus.offer,
            ApplicationStatus.accepted,
            ApplicationStatus.ghosted,
        )
    ]
    assert positions == sorted(positions)


def test_applications_partial_renders_empty_state(monkeypatch, client) -> None:
    """Render a useful message when there are no tracked applications."""

    def empty_applications(session: Session):
        return []

    monkeypatch.setattr(
        applications_route,
        "list_applications",
        empty_applications,
    )
    response = client.get("/applications/partials/list")

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
        sources = connection.execute(
            """
            SELECT name, enabled
            FROM sources
            ORDER BY name
            """
        ).fetchall()
        company_counts = connection.execute(
            "SELECT COUNT(*), SUM(is_sp500) FROM companies"
        ).fetchone()
        profile_rows = connection.execute(
            """
            SELECT p.role_name,
                   (SELECT COUNT(*) FROM profile_location_types plt
                    WHERE plt.profile_id = p.id),
                   (SELECT COUNT(*) FROM profile_keywords pk
                    WHERE pk.profile_id = p.id AND pk.kind = 'include'),
                   (SELECT COUNT(*) FROM profile_keywords pk
                    WHERE pk.profile_id = p.id AND pk.kind = 'exclude')
            FROM profiles p
            ORDER BY p.id
            """
        ).fetchall()
        query_rows = connection.execute(
            """
            SELECT p.role_name, s.name,
                   json_extract(psq.query_json, '$.category'),
                   COALESCE(
                     json_extract(psq.query_json, '$.search'),
                     json_extract(psq.query_json, '$.what')
                   ),
                   json_extract(psq.query_json, '$.limit')
            FROM profile_source_queries psq
            JOIN profiles p ON p.id = psq.profile_id
            JOIN sources s ON s.id = psq.source_id
            ORDER BY psq.id
            """
        ).fetchall()

    assert application == ("AI/ML Engineer", "Kavak", "pending", None)
    assert blacklist == (
        "Defense contractor — ethical concerns regarding military and surveillance contracts. Not aligned with personal values.",
        "PALANTIR TECHNOLOGIES INC A",
    )
    assert sources == [
        ("Adzuna", 1),
        ("Remotive", 1),
    ]
    assert company_counts == (502, 500)
    assert profile_rows == [
        ("AI Engineer", 2, 8, 3),
        ("Senior Fullstack Engineer", 1, 10, 3),
    ]
    assert query_rows == [
        ("AI Engineer", "Remotive", "artificial-intelligence", "AI engineer", 10),
        (
            "Senior Fullstack Engineer",
            "Remotive",
            "software-development",
            "fullstack",
            10,
        ),
        ("AI Engineer", "Adzuna", "it-jobs", "AI engineer", None),
        ("Senior Fullstack Engineer", "Adzuna", "it-jobs", "fullstack", None),
    ]
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
