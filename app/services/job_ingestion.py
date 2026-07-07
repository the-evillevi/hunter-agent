"""Idempotent persistence for normalized scraper results.

``scrape_jobs`` fetches and normalizes source records but deliberately writes
nothing. This module owns the persistence boundary between that normalized
output and the relational ``jobs`` table: it resolves the required profile,
company, location, and source foreign keys, skips records the database already
knows about, and reports what happened row by row.

Design choices (HNTR-15):

* Rediscovered jobs (matching ``hash`` or ``url``) are left untouched and
  counted as duplicates, so re-running a scrape is idempotent and preserves
  the original job IDs.
* Unknown companies and locations are created automatically from their
  normalized names. Sources are never created here: a job may only reference
  a source row that source enablement already persisted.
* Each record persists or fails on its own inside a savepoint. One malformed
  record cannot roll back the rest of the batch, and a failed record cannot
  leave behind a half-written job or orphaned lookup rows.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.company import Company
from app.models.job import Job
from app.models.location import Location
from app.models.profile import Profile
from app.models.source import Source
from app.services.sources import (
    NormalizedJob,
    make_job_identity_hash,
    normalize_source_name,
)
from app.services.sp500_company_import import normalize_name


@dataclass(frozen=True)
class JobIngestionFailure:
    """One normalized record that could not be persisted."""

    title: str
    company: str
    source_name: str
    reason: str


@dataclass
class JobIngestionSummary:
    """Structured result of one ingestion batch.

    A later manual trigger or scheduler (HNTR-51) can render this directly:
    what was inserted, what was skipped as an expected duplicate, and which
    rows failed with enough context to find the offending record.
    """

    inserted_job_ids: list[int] = field(default_factory=list)
    duplicate_count: int = 0
    failures: list[JobIngestionFailure] = field(default_factory=list)

    @property
    def inserted_count(self) -> int:
        return len(self.inserted_job_ids)

    @property
    def failed_count(self) -> int:
        return len(self.failures)


def ingest_normalized_jobs(
    session: Session,
    jobs: Sequence[NormalizedJob],
) -> JobIngestionSummary:
    """Persist normalized jobs idempotently and return a batch summary.

    The caller keeps fetching (``scrape_jobs``) and persistence as separate
    steps; this function only needs the normalized records and a session.
    """
    summary = JobIngestionSummary()
    lookups = _LookupResolver(session)
    source_cache: dict[str, Source | None] = {}

    for record in jobs:
        reason = _validation_failure_reason(session, record)
        if reason is not None:
            summary.failures.append(_failure(record, reason))
            continue

        source_row = _resolve_source(session, source_cache, record.source_name)
        if source_row is None:
            summary.failures.append(
                _failure(record, f"source '{record.source_name}' is not persisted")
            )
            continue

        # ``NormalizedJob.from_source`` always computes the hash, but a
        # hand-built record may not have one; fall back to the same helper.
        identity_hash = record.hash or make_job_identity_hash(
            source_name=record.source_name,
            external_id=record.external_id,
            url=record.url,
            title=record.title,
            company=record.company,
            location=record.location,
        )
        # A blank URL must become NULL: the ``jobs.url`` UNIQUE constraint
        # allows many NULLs but only one empty string.
        url = (record.url or "").strip() or None
        if _is_duplicate(session, identity_hash=identity_hash, url=url):
            summary.duplicate_count += 1
            continue

        try:
            # A savepoint per record: if the insert fails, only this record's
            # writes (including any lookup rows it created) are rolled back.
            with session.begin_nested():
                company = lookups.get_or_create(Company, record.company)
                location = lookups.get_or_create(Location, record.location)
                job_row = Job(
                    profile_id=record.profile_id,
                    title=record.title,
                    company_id=company.id,
                    location_id=location.id,
                    url=url,
                    source_id=source_row.id,
                    description=record.description,
                    hash=identity_hash,
                    scraped_at=record.scraped_at,
                    score=record.score,
                    score_reasoning=record.score_reasoning,
                )
                session.add(job_row)
                session.flush()
        except IntegrityError as error:
            lookups.discard_pending()
            if _is_unique_job_violation(error):
                # A race between the pre-check and the insert; still an
                # expected duplicate, not an error the caller must handle.
                summary.duplicate_count += 1
            else:
                summary.failures.append(_failure(record, str(error.orig or error)))
            continue

        lookups.confirm_pending()
        summary.inserted_job_ids.append(job_row.id)

    session.commit()
    return summary


class _LookupResolver:
    """Resolve companies and locations by normalized name, once per batch.

    Existing rows are cached immediately. Rows created for a record stay
    "pending" until that record's savepoint succeeds, so a failed record
    cannot leave cache entries pointing at rolled-back rows.
    """

    def __init__(self, session: Session) -> None:
        self._session = session
        self._cache: dict[tuple[type, str], Company | Location] = {}
        self._pending: dict[tuple[type, str], Company | Location] = {}

    def get_or_create(
        self,
        model: type[Company] | type[Location],
        name: str,
    ) -> Company | Location:
        key = (model, normalize_name(name))
        if key in self._cache:
            return self._cache[key]
        if key in self._pending:
            return self._pending[key]

        row = _find_named_row(self._session, model, name)
        if row is not None:
            self._cache[key] = row
            return row

        row = model(name=name.strip())
        self._session.add(row)
        # Flush so the new row has a primary key for the job's foreign key.
        self._session.flush()
        self._pending[key] = row
        return row

    def confirm_pending(self) -> None:
        self._cache.update(self._pending)
        self._pending.clear()

    def discard_pending(self) -> None:
        self._pending.clear()


def _find_named_row(
    session: Session,
    model: type[Company] | type[Location],
    name: str,
) -> Company | Location | None:
    """Match by whitespace-collapsed, casefolded name.

    Same rule as ``find_company_by_normalized_name`` in the S&P 500 import,
    generalized so companies and locations resolve identically.
    """
    normalized = normalize_name(name)
    for row in session.exec(select(model)).all():
        if normalize_name(row.name) == normalized:
            return row
    return None


def _validation_failure_reason(session: Session, record: NormalizedJob) -> str | None:
    """Return why a record cannot become a valid ``jobs`` row, or ``None``."""
    for field_name in ("title", "company", "location"):
        if not getattr(record, field_name).strip():
            return f"record has an empty {field_name}"
    if record.profile_id is None:
        return "record has no profile_id; jobs require a persisted profile"
    if session.get(Profile, record.profile_id) is None:
        return f"profile {record.profile_id} was not found"
    if record.score is not None and not 1 <= record.score <= 100:
        return f"score {record.score} is outside the allowed 1-100 range"
    return None


def _resolve_source(
    session: Session,
    cache: dict[str, Source | None],
    source_name: str,
) -> Source | None:
    """Match the adapter's source name against persisted source rows.

    The persisted row is the source of truth; any ``source_id`` carried on
    the normalized record is ignored on purpose.
    """
    normalized = normalize_source_name(source_name)
    if normalized not in cache:
        cache[normalized] = next(
            (
                source
                for source in session.exec(select(Source)).all()
                if normalize_source_name(source.name) == normalized
            ),
            None,
        )
    return cache[normalized]


def _is_duplicate(
    session: Session,
    *,
    identity_hash: str,
    url: str | None,
) -> bool:
    """Check the identity hash and URL against already-known jobs.

    Rows inserted earlier in this batch were flushed inside their released
    savepoints, so this in-transaction SELECT sees them too — batch-internal
    duplicates need no extra bookkeeping.
    """
    if session.exec(select(Job.id).where(Job.hash == identity_hash)).first():
        return True
    if url and session.exec(select(Job.id).where(Job.url == url)).first():
        return True
    return False


def _is_unique_job_violation(error: IntegrityError) -> bool:
    """Detect SQLite unique-constraint failures on the jobs dedupe columns."""
    message = str(error.orig or error)
    return "UNIQUE constraint failed" in message and (
        "jobs.hash" in message or "jobs.url" in message
    )


def _failure(record: NormalizedJob, reason: str) -> JobIngestionFailure:
    return JobIngestionFailure(
        title=record.title,
        company=record.company,
        source_name=record.source_name,
        reason=reason,
    )
