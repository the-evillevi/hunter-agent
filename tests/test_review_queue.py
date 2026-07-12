"""Service tests for the scored-job review queue (HNTR-2)."""

from sqlmodel import Session

from app.models.application import Application, ApplicationStatus
from app.services.blacklist import add_job_to_blacklist
from app.services.review_queue import (
    get_review_queue_item,
    get_review_run_detail,
    list_review_queue,
)


def make_application(session: Session, job, status: ApplicationStatus) -> Application:
    application = Application(job_id=job.id, status=status)
    session.add(application)
    session.commit()
    return application


def test_only_the_latest_run_per_job_counts(
    session, create_job, create_score_run
) -> None:
    job = create_job(title="Twice scored")
    create_score_run(job, score=40)
    create_score_run(job, score=90)

    page = list_review_queue(session)

    assert page.total == 1
    assert page.items[0].score == 90


def test_all_six_states_are_derived(session, create_job, create_score_run) -> None:
    scored = create_job(title="Scored")
    create_score_run(scored, score=80)

    ineligible = create_job(title="Ineligible")
    create_score_run(ineligible, status="rejected", score=None)

    failed = create_job(title="Failed")
    create_score_run(failed, status="failed", score=None)

    create_job(title="Unscored")

    drafted = create_job(title="Drafted")
    create_score_run(drafted, score=70)
    make_application(session, drafted, ApplicationStatus.draft)

    applied = create_job(title="Applied")
    create_score_run(applied, score=60)
    make_application(session, applied, ApplicationStatus.applied)

    states = {item.title: item.state for item in list_review_queue(session).items}

    assert states == {
        "Scored": "scored",
        "Ineligible": "ineligible",
        "Failed": "failed",
        "Unscored": "unscored",
        "Drafted": "drafted",
        "Applied": "applied",
    }


def test_profile_and_min_score_filters_compose(
    session, create_job, create_score_run
) -> None:
    high = create_job(title="High score")
    create_score_run(high, score=90)
    low = create_job(title="Low score")
    create_score_run(low, score=30)
    create_job(title="Never ran")

    page = list_review_queue(session, profile_id=high.profile_id, min_score=50)

    assert [item.title for item in page.items] == ["High score"]

    other_profile = list_review_queue(session, profile_id=9999, min_score=50)
    assert other_profile.total == 0


def test_ordering_is_deterministic_with_ties(
    session, create_job, create_score_run
) -> None:
    first = create_job(title="Tie A")
    second = create_job(title="Tie B")
    create_score_run(first, score=75)
    create_score_run(second, score=75)
    unscored = create_job(title="No score")

    page = list_review_queue(session)

    ids = [item.job_id for item in page.items]
    # Equal scores fall back to job id; unscored rows sort last.
    assert ids == [first.id, second.id, unscored.id]

    ascending = list_review_queue(session, descending=False)
    assert ascending.items[-1].job_id == unscored.id


def test_status_sort_ranks_outcome_progression_not_spelling(
    session, create_job, create_score_run
) -> None:
    """'offer' must outrank 'rejected' even though it sorts lower as text."""
    rejected = create_job(title="Rejected")
    create_score_run(rejected, score=50)
    make_application(session, rejected, ApplicationStatus.rejected)

    offer = create_job(title="Offer")
    create_score_run(offer, score=50)
    make_application(session, offer, ApplicationStatus.offer)

    drafted = create_job(title="Drafted")
    create_score_run(drafted, score=50)
    make_application(session, drafted, ApplicationStatus.draft)

    none = create_job(title="No application")
    create_score_run(none, score=50)

    descending = list_review_queue(session, sort="status")
    assert [item.title for item in descending.items] == [
        "Offer",
        "Rejected",
        "Drafted",
        "No application",
    ]


def test_pagination_math_and_out_of_range(
    session, create_job, create_score_run
) -> None:
    for index in range(3):
        job = create_job(title=f"Job {index}")
        create_score_run(job, score=50 + index)

    page = list_review_queue(session, page=1, page_size=2)
    assert page.total == 3
    assert page.total_pages == 2
    assert page.next_page == 2
    assert page.previous_page is None

    last = list_review_queue(session, page=2, page_size=2)
    assert len(last.items) == 1
    assert last.next_page is None

    beyond = list_review_queue(session, page=9, page_size=2)
    assert beyond.is_out_of_range
    assert beyond.previous_page == 2


def test_empty_database_yields_empty_page(session) -> None:
    page = list_review_queue(session)

    assert page.total == 0
    assert page.items == []
    assert page.total_pages == 0


def test_blacklisted_jobs_are_flagged(session, create_job, create_score_run) -> None:
    job = create_job(title="Blocked")
    create_score_run(job, score=88)
    add_job_to_blacklist(session, job_id=job.id, reason="scam")

    item = get_review_queue_item(session, job.id)

    assert item is not None
    assert item.blacklisted is True


def test_warning_count_comes_from_run_json(
    session, create_job, create_score_run
) -> None:
    job = create_job(title="Warned")
    create_score_run(job, score=66, warnings=["unchecked constraint: salary"])

    item = get_review_queue_item(session, job.id)

    assert item.warning_count == 1


def test_review_run_detail_returns_latest_run_and_layers(
    session, create_job, create_score_run
) -> None:
    from app.models.score_run import ScoreLayerResultRow

    job = create_job(title="Detailed")
    create_score_run(job, score=40)
    latest = create_score_run(job, score=85, warnings=["w1", "w2"])
    session.add(
        ScoreLayerResultRow(
            score_run_id=latest.id,
            layer="keyword",
            status="success",
            algorithm_version="1",
            score=85,
            explanation="matched 4/6 keywords",
        )
    )
    session.commit()

    detail = get_review_run_detail(session, job_id=job.id)

    assert detail is not None
    assert detail.run.id == latest.id
    assert [layer.layer for layer in detail.layers] == ["keyword"]
    assert detail.warnings == ["w1", "w2"]


def test_review_run_detail_none_for_unscored_job(
    session, create_job, create_score_run
) -> None:
    job = create_job(title="Fresh")

    assert get_review_run_detail(session, job_id=job.id) is None
