"""Blacklist mutations and lookups (HNTR-52).

This is the one boundary that writes the blacklist table and answers
"is this job blocked?". A job counts as blacklisted when either the job
itself or its company has an entry, so the application-draft guard and
the card badges agree on one definition.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.blacklist import Blacklist
from app.models.company import Company
from app.models.job import Job


class BlacklistError(ValueError):
    """Base class so routes can map blacklist failures uniformly."""


class BlacklistTargetNotFoundError(BlacklistError):
    """The job/company does not exist, or there is no entry to remove."""


class DuplicateBlacklistEntryError(BlacklistError):
    """The target already has a blacklist entry."""


class BlacklistedJobError(BlacklistError):
    """Raised by guards when a blocked job would gain an application."""


@dataclass(frozen=True)
class BlacklistFlag:
    """Display state for one job: how it is blocked, if at all."""

    kind: Literal["job", "company"] | None = None
    reason: str | None = None

    @property
    def blacklisted(self) -> bool:
        return self.kind is not None


def add_job_to_blacklist(
    session: Session,
    *,
    job_id: int,
    reason: str | None = None,
) -> Blacklist:
    """Block one job; duplicates and unknown jobs raise explicitly."""
    if session.get(Job, job_id) is None:
        raise BlacklistTargetNotFoundError(f"job {job_id} was not found")
    if _job_entry(session, job_id) is not None:
        raise DuplicateBlacklistEntryError(f"job {job_id} is already blacklisted")
    return _insert_entry(session, Blacklist(job_id=job_id, reason=_clean(reason)))


def add_company_to_blacklist(
    session: Session,
    *,
    company_id: int,
    reason: str | None = None,
) -> Blacklist:
    """Block a whole company; duplicates and unknown companies raise."""
    if session.get(Company, company_id) is None:
        raise BlacklistTargetNotFoundError(f"company {company_id} was not found")
    if _company_entry(session, company_id) is not None:
        raise DuplicateBlacklistEntryError(
            f"company {company_id} is already blacklisted"
        )
    return _insert_entry(
        session, Blacklist(company_id=company_id, reason=_clean(reason))
    )


def remove_job_from_blacklist(session: Session, *, job_id: int) -> None:
    """Delete the job's entry; a missing entry is an explicit error."""
    entry = _job_entry(session, job_id)
    if entry is None:
        raise BlacklistTargetNotFoundError(f"job {job_id} has no blacklist entry")
    session.delete(entry)
    session.commit()


def remove_company_from_blacklist(session: Session, *, company_id: int) -> None:
    """Delete the company's entry; a missing entry is an explicit error."""
    entry = _company_entry(session, company_id)
    if entry is None:
        raise BlacklistTargetNotFoundError(
            f"company {company_id} has no blacklist entry"
        )
    session.delete(entry)
    session.commit()


def is_job_blacklisted(session: Session, job_id: int) -> bool:
    """True when the job or its company has a blacklist entry."""
    if _job_entry(session, job_id) is not None:
        return True
    job = session.get(Job, job_id)
    if job is None:
        return False
    return _company_entry(session, job.company_id) is not None


def blacklist_flags(
    session: Session,
    job_company_pairs: Sequence[tuple[int, int]],
) -> dict[int, BlacklistFlag]:
    """Batch display flags for a job list without one query per row.

    ``job_company_pairs`` carries (job_id, company_id) so list queries that
    already joined the company avoid a second lookup here. Job-level
    entries win over company-level ones when both exist.
    """
    job_ids = [job_id for job_id, _ in job_company_pairs]
    company_ids = [company_id for _, company_id in job_company_pairs]

    job_entries: dict[int, Blacklist] = {}
    company_entries: dict[int, Blacklist] = {}
    if job_ids:
        for entry in session.exec(
            select(Blacklist).where(Blacklist.job_id.in_(job_ids))
        ).all():
            job_entries[entry.job_id] = entry
    if company_ids:
        for entry in session.exec(
            select(Blacklist).where(Blacklist.company_id.in_(company_ids))
        ).all():
            company_entries[entry.company_id] = entry

    flags: dict[int, BlacklistFlag] = {}
    for job_id, company_id in job_company_pairs:
        if job_id in job_entries:
            flags[job_id] = BlacklistFlag(kind="job", reason=job_entries[job_id].reason)
        elif company_id in company_entries:
            flags[job_id] = BlacklistFlag(
                kind="company", reason=company_entries[company_id].reason
            )
        else:
            flags[job_id] = BlacklistFlag()
    return flags


def _job_entry(session: Session, job_id: int) -> Blacklist | None:
    return session.exec(select(Blacklist).where(Blacklist.job_id == job_id)).first()


def _company_entry(session: Session, company_id: int) -> Blacklist | None:
    return session.exec(
        select(Blacklist).where(Blacklist.company_id == company_id)
    ).first()


def _insert_entry(session: Session, entry: Blacklist) -> Blacklist:
    job_id = entry.job_id
    company_id = entry.company_id
    session.add(entry)
    try:
        session.commit()
    except IntegrityError as error:
        session.rollback()
        if job_id is not None and _job_entry(session, job_id) is not None:
            raise DuplicateBlacklistEntryError(
                f"job {job_id} is already blacklisted"
            ) from error
        if company_id is not None and _company_entry(session, company_id) is not None:
            raise DuplicateBlacklistEntryError(
                f"company {company_id} is already blacklisted"
            ) from error
        raise
    session.refresh(entry)
    return entry


def _clean(reason: str | None) -> str | None:
    """Store empty reasons as NULL so the column stays meaningful."""
    if reason is None:
        return None
    return reason.strip() or None
