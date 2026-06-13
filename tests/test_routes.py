"""Smoke tests for FastAPI routes."""


def test_health_route_returns_ok(client) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_route_renders_jobs_page(client, create_application) -> None:
    create_application()

    response = client.get("/")

    assert response.status_code == 200
    assert "Tracked jobs" in response.text
    assert "Applied" in response.text
    assert "Avg score" in response.text
    assert "pending:" in response.text


def test_jobs_partial_renders_jobs_table(client, create_job) -> None:
    create_job(
        title="Senior Fullstack Engineer",
        company_name="Globant",
        location_name="Guadalajara, GDL",
        source_name="Remotive",
        score=82,
    )

    response = client.get("/jobs")

    assert response.status_code == 200
    assert "Senior Fullstack Engineer" in response.text
