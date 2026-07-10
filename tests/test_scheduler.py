"""Tests for the APScheduler wiring (HNTR-4).

No test starts a scheduler against real time: trigger math runs under a
frozen reference clock, job bodies are invoked directly, and the lifespan
test injects a fake scheduler. That keeps the suite fast and wall-time
independent.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

import app.main as app_main
import app.services.scheduler as scheduler_module
from app.main import app
from app.models.config import SchedulerConfig
from app.models.pipeline_run import PipelineRun
from app.services.pipeline import record_missed_pipeline_run
from app.services.scheduler import (
    PIPELINE_JOB_ID_PREFIX,
    build_pipeline_scheduler,
    next_scheduled_run_time,
    run_scheduled_pipeline,
)


TIMEZONE = "America/Mexico_City"


def make_config(
    *,
    enabled: bool = True,
    runs_at: list[str] | None = None,
) -> SchedulerConfig:
    return SchedulerConfig(
        enabled=enabled,
        runs_at=runs_at or ["08:00", "18:00"],
        timezone=TIMEZONE,
        lock_file="/tmp/hunter-agent-test.lock",
    )


async def fake_pipeline() -> None:  # pragma: no cover - replaced per test
    pass


def test_disabled_config_registers_no_scheduler() -> None:
    assert build_pipeline_scheduler(make_config(enabled=False), fake_pipeline) is None


def test_next_run_time_of_no_scheduler_is_none() -> None:
    assert next_scheduled_run_time(None) is None


def test_enabled_config_registers_one_cron_job_per_run_time() -> None:
    scheduler = build_pipeline_scheduler(make_config(), fake_pipeline)

    jobs = {job.id: job for job in scheduler.get_jobs()}
    assert set(jobs) == {
        f"{PIPELINE_JOB_ID_PREFIX}-0800",
        f"{PIPELINE_JOB_ID_PREFIX}-1800",
    }
    for job in jobs.values():
        assert str(job.trigger.timezone) == TIMEZONE
        assert job.coalesce is True
        assert job.misfire_grace_time == scheduler_module.MISFIRE_GRACE_SECONDS
        assert job.max_instances == 1


def test_next_run_time_picks_the_earliest_upcoming_slot() -> None:
    scheduler = build_pipeline_scheduler(make_config(), fake_pipeline)
    tz = ZoneInfo(TIMEZONE)

    morning = datetime(2026, 7, 10, 6, 30, tzinfo=tz)
    assert next_scheduled_run_time(scheduler, morning) == datetime(
        2026, 7, 10, 8, 0, tzinfo=tz
    )

    midday = datetime(2026, 7, 10, 8, 30, tzinfo=tz)
    assert next_scheduled_run_time(scheduler, midday) == datetime(
        2026, 7, 10, 18, 0, tzinfo=tz
    )

    # Just after a slot the next fire is the following one — a missed run
    # waits for the next scheduled slot rather than catching up.
    just_after_evening = datetime(2026, 7, 10, 18, 0, 1, tzinfo=tz)
    assert next_scheduled_run_time(scheduler, just_after_evening) == datetime(
        2026, 7, 11, 8, 0, tzinfo=tz
    )


def test_default_run_pipeline_is_the_scheduled_body() -> None:
    scheduler = build_pipeline_scheduler(make_config(runs_at=["08:00"]))
    assert scheduler.get_jobs()[0].func is run_scheduled_pipeline


def test_registered_job_awaits_the_injected_pipeline() -> None:
    calls: list[str] = []

    async def recording_pipeline() -> None:
        calls.append("ran")

    scheduler = build_pipeline_scheduler(
        make_config(runs_at=["08:00"]), recording_pipeline
    )
    job = scheduler.get_jobs()[0]

    asyncio.run(job.func())

    assert calls == ["ran"]


def test_default_job_body_runs_the_pipeline_as_scheduled(monkeypatch) -> None:
    captured: dict = {}

    async def fake_run_job_pipeline(session, *, trigger_type, **kwargs):
        captured["trigger_type"] = trigger_type
        return None, None

    monkeypatch.setattr(scheduler_module, "run_job_pipeline", fake_run_job_pipeline)

    asyncio.run(run_scheduled_pipeline())

    assert captured["trigger_type"] == "scheduled"


def test_lifespan_skips_scheduler_when_disabled_by_env() -> None:
    # conftest sets HUNTER_SCHEDULER_ENABLED=0 for the whole suite.
    with TestClient(app):
        assert app.state.scheduler is None


class FakeScheduler:
    def __init__(self) -> None:
        self.started = False
        self.shutdown_calls: list[bool] = []

    def start(self) -> None:
        self.started = True

    def shutdown(self, wait: bool = True) -> None:
        self.shutdown_calls.append(wait)


def test_lifespan_starts_and_stops_the_scheduler(monkeypatch) -> None:
    fake = FakeScheduler()
    monkeypatch.setenv("HUNTER_SCHEDULER_ENABLED", "1")
    monkeypatch.setattr(app_main, "build_pipeline_scheduler", lambda config: fake)

    with TestClient(app):
        assert fake.started is True
        assert app.state.scheduler is fake
        assert fake.shutdown_calls == []

    assert fake.shutdown_calls == [False]
    assert app.state.scheduler is None


def test_dashboard_panel_shows_next_scheduled_run(client, monkeypatch) -> None:
    scheduler = build_pipeline_scheduler(make_config(), fake_pipeline)
    monkeypatch.setattr(app.state, "scheduler", scheduler, raising=False)

    response = client.get("/pipeline/partials/runs")

    assert response.status_code == 200
    assert "Next scheduled run:" in response.text


def test_dashboard_panel_reports_disabled_scheduler(client) -> None:
    response = client.get("/pipeline/partials/runs")

    assert response.status_code == 200
    assert "Scheduler disabled" in response.text


def test_missed_run_is_persisted_as_distinct_skip(session) -> None:
    scheduled_at = datetime(2026, 7, 10, 8, 0)

    run = record_missed_pipeline_run(
        session,
        scheduled_at=scheduled_at,
        message="scheduled slot missed its misfire grace period",
    )

    assert run.status == "skipped_misfire"
    assert run.trigger_type == "scheduled"
    assert run.started_at == scheduled_at
    assert "misfire grace period" in run.errors


def test_missed_listener_records_the_scheduled_slot(monkeypatch) -> None:
    scheduled_at = datetime(2026, 7, 10, 8, 0, tzinfo=ZoneInfo(TIMEZONE))
    captured: dict = {}

    def fake_record(session, *, scheduled_at, message) -> PipelineRun:
        captured["scheduled_at"] = scheduled_at
        captured["message"] = message
        return PipelineRun(
            trigger_type="scheduled",
            status="skipped_misfire",
            started_at=scheduled_at,
        )

    monkeypatch.setattr(scheduler_module, "record_missed_pipeline_run", fake_record)

    scheduler_module._log_missed_run(
        SimpleNamespace(
            job_id=f"{PIPELINE_JOB_ID_PREFIX}-0800",
            scheduled_run_time=scheduled_at,
        )
    )

    assert captured["scheduled_at"] == scheduled_at.replace(tzinfo=None)
    assert scheduled_at.isoformat() in captured["message"]


def test_dashboard_panel_shows_misfire_status(client, session) -> None:
    record_missed_pipeline_run(
        session,
        scheduled_at=datetime(2026, 7, 10, 8, 0),
        message="missed",
    )

    response = client.get("/pipeline/partials/runs")

    assert response.status_code == 200
    assert "skipped (misfire)" in response.text
