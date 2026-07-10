from contextlib import asynccontextmanager
from importlib import metadata
import logging
import os
import tomllib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, load_config
from app.services.scheduler import build_pipeline_scheduler
from app.routes import (
    applications,
    companies,
    dashboard,
    health,
    jobs,
    pipeline,
    profiles,
    resumes,
    sources,
)


def app_version() -> str:
    """Read the installed package version so /docs always matches pyproject.

    The fallback keeps the app importable in environments where the package
    metadata is not installed (for example, a bare checkout without uv sync).
    """
    try:
        return metadata.version("hunter-agent")
    except metadata.PackageNotFoundError:
        try:
            with (PROJECT_ROOT / "pyproject.toml").open("rb") as project_file:
                project = tomllib.load(project_file).get("project", {})
        except OSError, tomllib.TOMLDecodeError:
            return "unknown"
        version = project.get("version")
        return (
            version.strip()
            if isinstance(version, str) and version.strip()
            else "unknown"
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start the pipeline scheduler with the app and stop it cleanly.

    ``HUNTER_SCHEDULER_ENABLED=0`` skips scheduling entirely — the test
    suite sets it so TestClient startups never boot a real scheduler.
    Reusing an already-attached scheduler keeps startup idempotent if the
    lifespan ever runs twice in one process (development reload quirks).
    """
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler is None and os.environ.get("HUNTER_SCHEDULER_ENABLED", "1") != "0":
        try:
            scheduler = build_pipeline_scheduler(load_config().scheduler)
            if scheduler is not None:
                scheduler.start()
        except Exception:
            # A broken config must not make the whole dashboard
            # unreachable; the app serves and the scheduler stays off.
            logging.getLogger(__name__).exception(
                "pipeline scheduler failed to start; continuing without it"
            )
            scheduler = None
    app.state.scheduler = scheduler
    try:
        yield
    finally:
        if scheduler is not None and getattr(scheduler, "running", True):
            scheduler.shutdown(wait=False)
        app.state.scheduler = None


# Swagger UI (/docs) and /openapi.json document the JSON API surface only.
# HTML and HTMX routes are excluded via include_in_schema=False because their
# audience is the browser UI, not API consumers; see the README for the policy.
app = FastAPI(
    lifespan=lifespan,
    title="Hunter Agent",
    description=(
        "Job application assistant. This API exposes the automation-friendly "
        "JSON operations: health monitoring and manual ingestion triggers. "
        "The HTML/HTMX user interface lives on the same server but is "
        "intentionally not part of this documented API."
    ),
    version=app_version(),
    openapi_tags=[
        {
            "name": "Monitoring",
            "description": "Liveness checks for uptime probes and deploys.",
        },
        {
            "name": "Ingestion",
            "description": "Manually triggered data ingestion runs.",
        },
        {
            "name": "Pipeline",
            "description": "End-to-end job pipeline runs through scoring.",
        },
    ],
)

# Static files are plain browser assets: CSS, images, and later small scripts.
app.mount("/static", StaticFiles(directory="app/static"), name="static")

# Routers keep `main.py` focused on wiring. Feature code lives in `app/routes/`.
app.include_router(health.router)
app.include_router(dashboard.router)
app.include_router(applications.router)
app.include_router(jobs.router)
app.include_router(profiles.router)
app.include_router(sources.router)
app.include_router(companies.router)
app.include_router(resumes.router)
app.include_router(pipeline.router)
