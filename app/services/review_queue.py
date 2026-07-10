"""Scored-job review queue queries (HNTR-2).

The review queue answers "which scored jobs deserve an application next?"
by pairing each job's latest score run with its application outcome (when
one exists) and blacklist state. Reads only: the explicit draft action
goes through applications.create_application_draft, and scoring execution
stays in the pipeline.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import case
from sqlmodel import Session, func, select

from app.models.application import Application, ApplicationStatus
from app.models.company import Company
from app.models.job import Job
from app.models.location import Location
from app.models.score_run import ScoreLayerResultRow, ScoreRun
from app.services.blacklist import blacklist_flags


ReviewState = Literal[
    "applied", "drafted", "ineligible", "failed", "unscored", "scored"
]
ReviewSort = Literal["score", "status"]

# Application statuses that mean "the user already acted on this job";
# draft and pending still count as in-progress review work.
ACTED_STATUSES = frozenset(
    {
        ApplicationStatus.applied,
        ApplicationStatus.acknowledged,
        ApplicationStatus.interviews,
        ApplicationStatus.rejected,
        ApplicationStatus.ghosted,
        ApplicationStatus.offer,
        ApplicationStatus.accepted,
    }
)


@dataclass(frozen=True)
class ReviewQueueItem:
    """One row of the review table: job, latest run, and outcome."""

    job_id: int
    company_id: int
    title: str
    company: str
    location: str
    profile_id: int
    run_id: int | None
    run_status: str | None  # scored | rejected | failed | None (never run)
    score: int | None
    pipeline_version: str | None
    run_created_at: datetime | None
    warning_count: int
    application_id: int | None
    application_status: ApplicationStatus | None
    blacklisted: bool
    state: ReviewState


@dataclass(frozen=True)
class ReviewQueuePage:
    """One bounded page of review items and its navigation metadata."""

    items: list[ReviewQueueItem]
    page: int
    page_size: int
    total: int
    total_pages: int
    previous_page: int | None
    next_page: int | None

    @property
    def is_out_of_range(self) -> bool:
        return self.total_pages > 0 and self.page > self.total_pages


@dataclass(frozen=True)
class ReviewRunDetail:
    """Everything inspectable about a job's latest score run."""

    run: ScoreRun
    layers: list[ScoreLayerResultRow]
    warnings: list[str]
    eligibility_reasons: list[dict]
    unknowns: list[str]


def list_review_queue(
    session: Session,
    *,
    profile_id: int | None = None,
    min_score: int | None = None,
    sort: ReviewSort = "score",
    descending: bool = True,
    page: int = 1,
    page_size: int = 50,
) -> ReviewQueuePage:
    """Return a filtered, deterministically ordered page of review items.

    ``min_score`` implies "scored at least this well", so it hides jobs
    whose latest run is rejected/failed or that never ran. Whatever the
    chosen sort, score and job id always close the ordering so ties are
    stable across requests.
    """
    filters = []
    if profile_id is not None:
        filters.append(Job.profile_id == profile_id)
    if min_score is not None:
        filters.append(ScoreRun.score >= min_score)

    count_statement = select(func.count()).select_from(
        _review_join(select(Job.id)).subquery()
    )
    statement = _review_join(_review_select())
    if filters:
        count_statement = select(func.count()).select_from(
            _review_join(select(Job.id).where(*filters)).subquery()
        )
        statement = statement.where(*filters)

    total = session.exec(count_statement).one()
    total_pages = (total + page_size - 1) // page_size

    order = _order_by(sort, descending)
    rows = session.exec(
        statement.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = _items_from_rows(session, rows)

    if total_pages and page > total_pages:
        previous_page = total_pages
    elif page > 1:
        previous_page = page - 1
    else:
        previous_page = None

    return ReviewQueuePage(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
        total_pages=total_pages,
        previous_page=previous_page,
        next_page=page + 1 if page < total_pages else None,
    )


def get_review_queue_item(session: Session, job_id: int) -> ReviewQueueItem | None:
    """Return one job's review row, for single-row fragment refreshes."""
    row = session.exec(_review_join(_review_select()).where(Job.id == job_id)).first()
    if row is None:
        return None
    return _items_from_rows(session, [row])[0]


def get_review_run_detail(session: Session, *, job_id: int) -> ReviewRunDetail | None:
    """Return the latest run's layer rows and parsed JSON evidence."""
    run = session.exec(
        select(ScoreRun).where(ScoreRun.job_id == job_id).order_by(ScoreRun.id.desc())
    ).first()
    if run is None:
        return None
    layers = list(
        session.exec(
            select(ScoreLayerResultRow)
            .where(ScoreLayerResultRow.score_run_id == run.id)
            .order_by(ScoreLayerResultRow.id)
        ).all()
    )
    return ReviewRunDetail(
        run=run,
        layers=layers,
        warnings=_parse_json_list(run.warnings),
        eligibility_reasons=_parse_json_list(run.eligibility_reasons),
        unknowns=_parse_json_list(run.unknowns),
    )


def _review_select():
    return select(
        Job.id,
        Job.company_id,
        Job.profile_id,
        Job.title,
        Company.name,
        Location.name,
        ScoreRun.id,
        ScoreRun.status,
        ScoreRun.score,
        ScoreRun.pipeline_version,
        ScoreRun.created_at,
        ScoreRun.warnings,
        Application.id,
        Application.status,
    )


def _review_join(statement):
    """Join jobs to their latest score run and application, if any."""
    latest_runs = (
        select(ScoreRun.job_id.label("job_id"), func.max(ScoreRun.id).label("run_id"))
        .group_by(ScoreRun.job_id)
        .subquery()
    )
    return (
        statement.join(Company, Company.id == Job.company_id)
        .join(Location, Location.id == Job.location_id)
        .join(latest_runs, latest_runs.c.job_id == Job.id, isouter=True)
        .join(ScoreRun, ScoreRun.id == latest_runs.c.run_id, isouter=True)
        .join(Application, Application.job_id == Job.id, isouter=True)
    )


def _order_by(sort: ReviewSort, descending: bool):
    score_nulls_last = case((ScoreRun.score.is_(None), 1), else_=0)
    score_order = ScoreRun.score.desc() if descending else ScoreRun.score.asc()
    if sort == "status":
        status_key = func.coalesce(Application.status, "")
        primary = [status_key.desc() if descending else status_key.asc()]
    else:
        primary = []
    # Score and job id always close the ordering so ties are stable.
    return [*primary, score_nulls_last, score_order, Job.id.asc()]


def _items_from_rows(session: Session, rows) -> list[ReviewQueueItem]:
    pairs = [(row[0], row[1]) for row in rows]
    flags = blacklist_flags(session, pairs)
    items = []
    for row in rows:
        (
            job_id,
            company_id,
            profile_id,
            title,
            company,
            location,
            run_id,
            run_status,
            score,
            pipeline_version,
            run_created_at,
            warnings_json,
            application_id,
            application_status,
        ) = row
        blacklisted = flags[job_id].blacklisted
        items.append(
            ReviewQueueItem(
                job_id=job_id,
                company_id=company_id,
                title=title,
                company=company,
                location=location,
                profile_id=profile_id,
                run_id=run_id,
                run_status=run_status,
                score=score,
                pipeline_version=pipeline_version,
                run_created_at=run_created_at,
                warning_count=len(_parse_json_list(warnings_json)),
                application_id=application_id,
                application_status=application_status,
                blacklisted=blacklisted,
                state=_derive_state(run_status, application_status),
            )
        )
    return items


def _derive_state(
    run_status: str | None,
    application_status: ApplicationStatus | None,
) -> ReviewState:
    """One label per row so distinct situations stay visibly distinct.

    The application outcome wins over scoring evidence: once the user
    acted on a job, how it scored is history.
    """
    if application_status is not None:
        if application_status in ACTED_STATUSES:
            return "applied"
        return "drafted"
    if run_status is None:
        return "unscored"
    if run_status == "rejected":
        return "ineligible"
    if run_status == "failed":
        return "failed"
    return "scored"


def _parse_json_list(payload: str | None) -> list:
    if not payload:
        return []
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []
