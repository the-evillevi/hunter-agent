"""Database tests for the idempotent job ingestion service.

Every test builds fake ``NormalizedJob`` records directly, so no adapter code
runs and no network calls happen. The ``session`` fixture provides an isolated
per-test SQLite database.
"""

from collections.abc import Callable

import pytest
from sqlmodel import Session, select

from app.models.company import Company
from app.models.job import Job
from app.models.location import Location
from app.models.profile import Profile
from app.models.source import Source
from app.services.job_ingestion import ingest_normalized_jobs
from app.services.sources import JobSourceIdentity, NormalizedJob


REMOTIVE_IDENTITY = JobSourceIdentity(name="remotive", display_name="Remotive")


@pytest.fixture()
def profile(session: Session) -> Profile:
    row = Profile(role_name="AI Engineer", salary_min=0, match_threshold=80)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture()
def remotive_source(session: Session) -> Source:
    # Stored with a different case than the adapter identity ("remotive") on
    # purpose: resolution must match by normalized name, like the registry.
    row = Source(name="Remotive", enabled=True)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@pytest.fixture()
def make_record(profile: Profile) -> Callable[..., NormalizedJob]:
    """Build a valid normalized record; keyword overrides tweak one aspect."""

    def _make_record(
        *,
        title: str = "Backend Engineer",
        company: str = "Acme Corp",
        location: str = "Remote",
        url: str | None = "https://example.test/jobs/backend-engineer",
        external_id: str | None = None,
        profile_id: int | None = profile.id,
        **overrides,
    ) -> NormalizedJob:
        return NormalizedJob.from_source(
            source=REMOTIVE_IDENTITY,
            title=title,
            company=company,
            location=location,
            url=url,
            external_id=external_id,
            profile_id=profile_id,
            **overrides,
        )

    return _make_record


def test_first_ingestion_persists_jobs_with_resolved_foreign_keys(
    session, profile, remotive_source, make_record
) -> None:
    records = [
        make_record(description="Build APIs."),
        make_record(
            title="Data Engineer",
            company="Globex",
            location="CDMX",
            url="https://example.test/jobs/data-engineer",
        ),
    ]

    summary = ingest_normalized_jobs(session, records)

    assert summary.inserted_count == 2
    assert summary.duplicate_count == 0
    assert summary.failed_count == 0

    first = session.get(Job, summary.inserted_job_ids[0])
    assert first.profile_id == profile.id
    assert first.source_id == remotive_source.id
    assert first.description == "Build APIs."
    assert first.hash == records[0].hash
    company = session.get(Company, first.company_id)
    location = session.get(Location, first.location_id)
    assert company.name == "Acme Corp"
    assert location.name == "Remote"


def test_reingesting_the_same_batch_is_idempotent(
    session, remotive_source, make_record
) -> None:
    records = [
        make_record(),
        make_record(
            title="Data Engineer", url="https://example.test/jobs/data-engineer"
        ),
    ]
    first_summary = ingest_normalized_jobs(session, records)

    second_summary = ingest_normalized_jobs(session, records)

    assert second_summary.inserted_count == 0
    assert second_summary.duplicate_count == 2
    assert second_summary.failed_count == 0
    surviving_ids = sorted(session.exec(select(Job.id)).all())
    assert surviving_ids == sorted(first_summary.inserted_job_ids)


def test_rediscovered_job_is_left_unchanged(
    session, remotive_source, make_record
) -> None:
    original = make_record(external_id="job-1", description="Original text.")
    ingest_normalized_jobs(session, [original])

    refreshed = make_record(external_id="job-1", description="Newer text.")
    summary = ingest_normalized_jobs(session, [refreshed])

    assert summary.duplicate_count == 1
    stored = session.exec(select(Job)).one()
    assert stored.description == "Original text."


def test_same_hash_with_different_url_is_a_duplicate(
    session, remotive_source, make_record
) -> None:
    # With an external_id the hash ignores the URL, so a moved posting keeps
    # the same identity.
    ingest_normalized_jobs(
        session,
        [make_record(external_id="job-1", url="https://example.test/jobs/1")],
    )

    summary = ingest_normalized_jobs(
        session,
        [make_record(external_id="job-1", url="https://example.test/jobs/1-moved")],
    )

    assert summary.duplicate_count == 1
    assert summary.inserted_count == 0


def test_same_url_with_different_hash_is_a_duplicate(
    session, remotive_source, make_record
) -> None:
    url = "https://example.test/jobs/shared"
    ingest_normalized_jobs(session, [make_record(external_id="job-1", url=url)])

    summary = ingest_normalized_jobs(
        session, [make_record(external_id="job-2", url=url)]
    )

    assert summary.duplicate_count == 1
    assert summary.inserted_count == 0


def test_batch_internal_duplicates_insert_once(
    session, remotive_source, make_record
) -> None:
    records = [make_record(), make_record()]

    summary = ingest_normalized_jobs(session, records)

    assert summary.inserted_count == 1
    assert summary.duplicate_count == 1


def test_company_and_location_resolution_is_case_insensitive(
    session, remotive_source, make_record
) -> None:
    existing_company = Company(name="Acme Corp")
    existing_location = Location(name="Remote")
    session.add(existing_company)
    session.add(existing_location)
    session.commit()

    summary = ingest_normalized_jobs(
        session, [make_record(company="acme  corp", location="REMOTE")]
    )

    job = session.get(Job, summary.inserted_job_ids[0])
    assert job.company_id == existing_company.id
    assert job.location_id == existing_location.id
    assert len(session.exec(select(Company)).all()) == 1
    assert len(session.exec(select(Location)).all()) == 1


def test_unknown_companies_and_locations_are_created(
    session, remotive_source, make_record
) -> None:
    summary = ingest_normalized_jobs(
        session, [make_record(company="  Initech ", location="Austin, TX")]
    )

    assert summary.inserted_count == 1
    company = session.exec(select(Company)).one()
    location = session.exec(select(Location)).one()
    assert company.name == "Initech"  # stored stripped, original casing kept
    assert location.name == "Austin, TX"


def test_blank_urls_are_stored_as_null_and_do_not_collide(
    session, remotive_source, make_record
) -> None:
    # jobs.url is UNIQUE but nullable: many NULLs may coexist, while two
    # empty strings would collide. Blank URLs must therefore become NULL.
    records = [
        make_record(url="  ", external_id="job-1"),
        make_record(url=None, external_id="job-2", title="Data Engineer"),
    ]

    summary = ingest_normalized_jobs(session, records)

    assert summary.inserted_count == 2
    assert [job.url for job in session.exec(select(Job)).all()] == [None, None]


def test_record_without_profile_fails_with_context(
    session, remotive_source, make_record
) -> None:
    summary = ingest_normalized_jobs(session, [make_record(profile_id=None)])

    assert summary.inserted_count == 0
    assert summary.failed_count == 1
    failure = summary.failures[0]
    assert failure.title == "Backend Engineer"
    assert failure.company == "Acme Corp"
    assert failure.source_name == "remotive"
    assert "profile" in failure.reason


def test_unknown_profile_fails_the_row(session, remotive_source, make_record) -> None:
    summary = ingest_normalized_jobs(session, [make_record(profile_id=999)])

    assert summary.failed_count == 1
    assert "999" in summary.failures[0].reason


def test_unknown_source_fails_the_row_and_is_not_created(
    session, profile, make_record
) -> None:
    # No Source rows exist at all: sources are owned by enablement, so
    # ingestion must refuse rather than invent one.
    summary = ingest_normalized_jobs(session, [make_record()])

    assert summary.failed_count == 1
    assert "remotive" in summary.failures[0].reason
    assert session.exec(select(Source)).all() == []


def test_out_of_range_score_fails_the_row(
    session, remotive_source, make_record
) -> None:
    summary = ingest_normalized_jobs(session, [make_record(score=150)])

    assert summary.failed_count == 1
    assert "150" in summary.failures[0].reason


def test_mixed_batch_persists_valid_rows_despite_failures(
    session, remotive_source, make_record
) -> None:
    records = [
        make_record(profile_id=999, company="Bad Row Co"),
        make_record(
            title="Data Engineer", url="https://example.test/jobs/data-engineer"
        ),
    ]

    summary = ingest_normalized_jobs(session, records)

    assert summary.inserted_count == 1
    assert summary.failed_count == 1
    stored = session.exec(select(Job)).one()
    assert stored.title == "Data Engineer"
    # The failed row's company was never persisted alongside a missing job.
    company_names = {company.name for company in session.exec(select(Company)).all()}
    assert "Bad Row Co" not in company_names
