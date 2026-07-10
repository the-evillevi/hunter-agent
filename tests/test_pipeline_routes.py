"""Route tests for the pipeline trigger endpoints and recent-runs panel."""

from datetime import datetime

import pytest
from sqlmodel import Session

import app.routes.pipeline as pipeline_routes
from app.models.pipeline_run import PipelineRun
from app.services.pipeline import PipelineRunSummary


@pytest.fixture()
def fake_run(monkeypatch):
    """Replace the orchestrator so route tests never touch real stages."""

    def _install(status: str = "success", **counts):
        async def fake_run_job_pipeline(session, *, trigger_type, **kwargs):
            summary = PipelineRunSummary(
                trigger_type=trigger_type,
                started_at=datetime(2026, 7, 10, 8, 0),
            )
            for key, value in counts.items():
                setattr(summary, key, value)
            summary.status = status
            summary.finished_at = datetime(2026, 7, 10, 8, 5)
            if status in ("failure", "skipped_overlap"):
                summary.add_error("run", "canned error for the test")
            run = PipelineRun(
                trigger_type=trigger_type,
                status=summary.status,
                started_at=summary.started_at,
                finished_at=summary.finished_at,
            )
            return run, summary

        monkeypatch.setattr(pipeline_routes, "run_job_pipeline", fake_run_job_pipeline)

    return _install


def make_run(session: Session, **overrides) -> PipelineRun:
    values = {
        "trigger_type": "manual",
        "status": "success",
        "started_at": datetime(2026, 7, 10, 8, 0),
        "finished_at": datetime(2026, 7, 10, 8, 4),
        "discovered_count": 5,
        "persisted_count": 3,
        "scored_count": 3,
    }
    values.update(overrides)
    run = PipelineRun(**values)
    session.add(run)
    session.commit()
    return run


def test_manual_trigger_renders_success_fragment(client, fake_run) -> None:
    fake_run("success", discovered=4, persisted=3, scored=3)

    response = client.post("/pipeline/run")

    assert response.status_code == 200
    assert "Pipeline run completed" in response.text
    assert "Discovered 4" in response.text


def test_manual_trigger_renders_skipped_overlap_as_conflict(client, fake_run) -> None:
    fake_run("skipped_overlap")

    response = client.post("/pipeline/run")

    assert response.status_code == 409
    assert "another run is already in progress" in response.text


def test_api_twin_returns_json_summary(client, fake_run) -> None:
    fake_run("partial", discovered=2, persisted=1, scored=1)

    response = client.post("/api/pipeline/run")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "partial"
    assert payload["trigger_type"] == "manual"
    assert payload["discovered"] == 2


def test_api_twin_maps_failure_to_500(client, fake_run) -> None:
    fake_run("failure")

    response = client.post("/api/pipeline/run")

    assert response.status_code == 500
    assert response.json()["status"] == "failure"


def test_runs_partial_lists_recent_runs(client, session) -> None:
    make_run(session, status="success")
    make_run(session, status="partial", started_at=datetime(2026, 7, 10, 9, 0))

    response = client.get("/pipeline/partials/runs")

    assert response.status_code == 200
    assert "success" in response.text
    assert "partial" in response.text


def test_runs_partial_shows_empty_state(client) -> None:
    response = client.get("/pipeline/partials/runs")

    assert response.status_code == 200
    assert "No pipeline runs yet" in response.text


def test_dashboard_includes_pipeline_panel(client, session) -> None:
    make_run(session, status="skipped_overlap")

    response = client.get("/")

    assert response.status_code == 200
    assert "Pipeline runs" in response.text
    assert "skipped (overlap)" in response.text
