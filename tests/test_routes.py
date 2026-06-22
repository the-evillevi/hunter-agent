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
    assert "Job sources" in response.text
    assert "Adzuna" in response.text
    assert "Applied" in response.text
    assert "Avg score" in response.text
    assert "pending:" in response.text
    assert 'href="/companies"' in response.text
    assert 'hx-get="/jobs/partials/list"' in response.text
    assert 'hx-get="/sources/partials/list"' in response.text
    assert 'hx-get="/applications/partials/list"' in response.text
    assert 'hx-get="/jobs"' not in response.text
    assert 'hx-get="/sources"' not in response.text
    assert 'hx-get="/applications"' not in response.text


def test_jobs_partial_renders_jobs_table(client, create_job) -> None:
    create_job(
        title="Senior Fullstack Engineer",
        company_name="Globant",
        location_name="Guadalajara, GDL",
        source_name="Remotive",
        score=82,
    )

    response = client.get("/jobs/partials/list")

    assert response.status_code == 200
    assert "Senior Fullstack Engineer" in response.text


def test_obsolete_partial_routes_return_not_found(client) -> None:
    for path in ("/jobs", "/sources", "/applications"):
        response = client.get(path)

        assert response.status_code == 404
