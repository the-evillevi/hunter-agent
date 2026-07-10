"""Service and model tests for the blacklist boundary (HNTR-52)."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models.blacklist import Blacklist
from app.services.blacklist import (
    BlacklistTargetNotFoundError,
    DuplicateBlacklistEntryError,
    add_company_to_blacklist,
    add_job_to_blacklist,
    blacklist_flags,
    is_job_blacklisted,
    remove_company_from_blacklist,
    remove_job_from_blacklist,
)


def test_blacklist_rejects_rows_without_exactly_one_target(session: Session) -> None:
    with pytest.raises(IntegrityError):
        session.add(Blacklist(job_id=None, company_id=None))
        session.commit()
    session.rollback()


def test_blacklist_rejects_rows_with_both_targets(session, create_job) -> None:
    job = create_job()
    with pytest.raises(IntegrityError):
        session.add(Blacklist(job_id=job.id, company_id=job.company_id))
        session.commit()
    session.rollback()


def test_add_job_entry_persists_reason_and_timestamp(session, create_job) -> None:
    job = create_job()

    entry = add_job_to_blacklist(session, job_id=job.id, reason="  low salary  ")

    assert entry.id is not None
    assert entry.job_id == job.id
    assert entry.company_id is None
    assert entry.reason == "low salary"
    assert entry.added_at is not None


def test_blank_reason_is_stored_as_null(session, create_job) -> None:
    job = create_job()

    entry = add_job_to_blacklist(session, job_id=job.id, reason="   ")

    assert entry.reason is None


def test_duplicate_job_entry_raises(session, create_job) -> None:
    job = create_job()
    add_job_to_blacklist(session, job_id=job.id)

    with pytest.raises(DuplicateBlacklistEntryError):
        add_job_to_blacklist(session, job_id=job.id)


def test_unknown_targets_raise(session) -> None:
    with pytest.raises(BlacklistTargetNotFoundError):
        add_job_to_blacklist(session, job_id=999)
    with pytest.raises(BlacklistTargetNotFoundError):
        add_company_to_blacklist(session, company_id=999)


def test_remove_deletes_the_row(session, create_job) -> None:
    job = create_job()
    add_job_to_blacklist(session, job_id=job.id)

    remove_job_from_blacklist(session, job_id=job.id)

    assert is_job_blacklisted(session, job.id) is False


def test_remove_missing_entry_is_an_explicit_error(session, create_job) -> None:
    job = create_job()

    with pytest.raises(BlacklistTargetNotFoundError):
        remove_job_from_blacklist(session, job_id=job.id)
    with pytest.raises(BlacklistTargetNotFoundError):
        remove_company_from_blacklist(session, company_id=job.company_id)


def test_job_is_blacklisted_via_its_company(session, create_job) -> None:
    job = create_job(company_name="Blocked Corp")
    add_company_to_blacklist(session, company_id=job.company_id, reason="culture")

    assert is_job_blacklisted(session, job.id) is True

    remove_company_from_blacklist(session, company_id=job.company_id)
    assert is_job_blacklisted(session, job.id) is False


def test_blacklist_flags_batch_overlay(session, create_job) -> None:
    blocked_job = create_job(title="Blocked job", company_name="Fine Co")
    company_blocked = create_job(title="Other", company_name="Blocked Corp")
    clean = create_job(title="Clean", company_name="Nice Co")
    add_job_to_blacklist(session, job_id=blocked_job.id, reason="spam")
    add_company_to_blacklist(session, company_id=company_blocked.company_id)

    flags = blacklist_flags(
        session,
        [
            (blocked_job.id, blocked_job.company_id),
            (company_blocked.id, company_blocked.company_id),
            (clean.id, clean.company_id),
        ],
    )

    assert flags[blocked_job.id].kind == "job"
    assert flags[blocked_job.id].reason == "spam"
    assert flags[company_blocked.id].kind == "company"
    assert flags[clean.id].blacklisted is False


def test_job_level_entry_wins_over_company_entry(session, create_job) -> None:
    job = create_job(company_name="Both Corp")
    add_company_to_blacklist(session, company_id=job.company_id, reason="company")
    add_job_to_blacklist(session, job_id=job.id, reason="job")

    flags = blacklist_flags(session, [(job.id, job.company_id)])

    assert flags[job.id].kind == "job"
    assert flags[job.id].reason == "job"
