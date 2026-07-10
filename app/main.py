from importlib import metadata
import tomllib

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT
from app.routes import (
    applications,
    companies,
    dashboard,
    health,
    jobs,
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


# Swagger UI (/docs) and /openapi.json document the JSON API surface only.
# HTML and HTMX routes are excluded via include_in_schema=False because their
# audience is the browser UI, not API consumers; see the README for the policy.
app = FastAPI(
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
