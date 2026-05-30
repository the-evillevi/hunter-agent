"""Smoke tests for FastAPI routes."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_route_returns_ok() -> None:
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_home_route_renders_jobs_page() -> None:
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Tracked jobs" in response.text


def test_jobs_partial_renders_job_cards() -> None:
    client = TestClient(app)

    response = client.get("/jobs")

    assert response.status_code == 200
    assert "Senior Fullstack Engineer" in response.text
