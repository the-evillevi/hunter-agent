"""Scraping orchestration.

Concrete source adapters live behind ``app.services.sources.JobSourceAdapter``.
This module coordinates registered adapters and deliberately does not persist
results yet; HNTR-15 can decide how to use the identity hash for deduplication.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from sqlmodel import Session

from app.db.database import engine
from app.services import remotive  # noqa: F401
from app.services.sources import (
    JobSourceRegistry,
    JobSourceRunContext,
    NormalizedJob,
    default_source_registry,
)


async def scrape_jobs_async(
    *,
    registry: JobSourceRegistry | None = None,
    source_names: Iterable[str] | None = None,
    context: JobSourceRunContext | None = None,
    session: Session | None = None,
) -> list[NormalizedJob]:
    """Fetch and normalize jobs from selected or DB-enabled sources."""
    source_registry = registry or default_source_registry
    run_context = context or JobSourceRunContext()

    if source_names is not None:
        adapters = source_registry.resolve_selected(source_names)
    elif session is not None:
        adapters = source_registry.resolve_enabled(session)
    else:
        with Session(engine) as db_session:
            adapters = source_registry.resolve_enabled(db_session)

    normalized_jobs: list[NormalizedJob] = []
    for resolved_source in adapters:
        adapter = resolved_source.adapter
        raw_jobs = await adapter.fetch(run_context)
        normalized_jobs.extend(
            adapter.normalize(raw_job, run_context) for raw_job in raw_jobs
        )
    return normalized_jobs


def scrape_jobs(
    *,
    registry: JobSourceRegistry | None = None,
    source_names: Iterable[str] | None = None,
    context: JobSourceRunContext | None = None,
    session: Session | None = None,
) -> list[dict]:
    """Return scraped jobs as dictionaries for existing sync callers."""
    jobs = asyncio.run(
        scrape_jobs_async(
            registry=registry,
            source_names=source_names,
            context=context,
            session=session,
        )
    )
    return [job.to_job_dict() for job in jobs]
