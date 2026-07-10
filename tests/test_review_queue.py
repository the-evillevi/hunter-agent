"""Service tests for the scored-job review queue (HNTR-2)."""

import json
from datetime import datetime

from sqlmodel import Session

from app.models.application import Application, ApplicationStatus
from app.models.score_run import ScoreRun
from app.services.blacklist import add_job_to_blacklist
from app.services.review_queue import (
    get_review_queue_item,
    get_review_run_detail,
    list_review_queue,
)


def make_run(
    session: Session,
    job,
    *,
    status: str = "scored",
    score: int | None = 80,
    warnings: list[str] | None = None,
) -> ScoreRun:
    run = ScoreRun(
        job_id=job.id,
        profile_id=job.profile_id,
        pipeline_version="1",
        weights_version="1",
        status=status,
        score=score if status == "scored" else None,
        warnings=json.dumps(warnings or []),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def make_application(session: Session, job, status: ApplicationStatus) -> Application:
    application = Application(job_id=job.id, status=status)
    session.add(application)
    session.commit()
    return application


def test_only_the_latest_run_per_job_counts(session, create_job) -> None:
    job = create_job(title="Twice scored")
    make_run(session, job, score=40)
    make_run(session, job, score=90)

    page = list_review_queue(session)

    assert page.total == 1
    assert page.items[0].score == 90


def test_all_six_states_are_derived(session, create_job) -> None:
    scored = create_job(title="Scored")
    make_run(session, scored, score=80)

    ineligible = create_job(title="Ineligible")
    make_run(session, ineligible, status="rejected", score=None)

    failed = create_job(title="Failed")
    make_run(session, failed, status="failed", score=None)

    create_job(title="Unscored")

    drafted = create_job(title="Drafted")
    make_run(session, drafted, score=70)
    make_application(session, drafted, ApplicationStatus.draft)

    applied = create_job(title="Applied")
    make_run(session, applied, score=60)
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


def test_profile_and_min_score_filters_compose(session, create_job) -> None:
    high = create_job(title="High score")
    make_run(session, high, score=90)
    low = create_job(title="Low score")
    make_run(session, low, score=30)
    create_job(title="Never ran")

    page = list_review_queue(session, profile_id=high.profile_id, min_score=50)

    assert [item.title for item in page.items] == ["High score"]

    other_profile = list_review_queue(session, profile_id=9999, min_score=50)
    assert other_profile.total == 0


def test_ordering_is_deterministic_with_ties(session, create_job) -> None:
    first = create_job(title="Tie A")
    second = create_job(title="Tie B")
    make_run(session, first, score=75)
    make_run(session, second, score=75)
    unscored = create_job(title="No score")

    page = list_review_queue(session)

    ids = [item.job_id for item in page.items]
    # Equal scores fall back to job id; unscored rows sort last.
    assert ids == [first.id, second.id, unscored.id]

    ascending = list_review_queue(session, descending=False)
    assert ascending.items[-1].job_id == unscored.id


def test_status_sort_orders_by_application_outcome(session, create_job) -> None:
    drafted = create_job(title="Drafted")
    make_run(session, drafted, score=50)
    make_application(session, drafted, ApplicationStatus.draft)

    applied = create_job(title="Applied")
    make_run(session, applied, score=50)
    make_application(session, applied, ApplicationStatus.applied)

    none = create_job(title="No application")
    make_run(session, none, score=50)

    descending = list_review_queue(session, sort="status")
    assert [item.title for item in descending.items] == [
        "Drafted",
        "Applied",
        "No application",
    ]


def test_pagination_math_and_out_of_range(session, create_job) -> None:
    for index in range(3):
        job = create_job(title=f"Job {index}")
        make_run(session, job, score=50 + index)

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


def test_blacklisted_jobs_are_flagged(session, create_job) -> None:
    job = create_job(title="Blocked")
    make_run(session, job, score=88)
    add_job_to_blacklist(session, job_id=job.id, reason="scam")

    item = get_review_queue_item(session, job.id)

    assert item is not None
    assert item.blacklisted is True


def test_warning_count_comes_from_run_json(session, create_job) -> None:
    job = create_job(title="Warned")
    make_run(session, job, score=66, warnings=["unchecked constraint: salary"])

    item = get_review_queue_item(session, job.id)

    assert item.warning_count == 1


def test_review_run_detail_returns_latest_run_and_layers(session, create_job) -> None:
    from app.models.score_run import ScoreLayerResultRow

    job = create_job(title="Detailed")
    make_run(session, job, score=40)
    latest = make_run(session, job, score=85, warnings=["w1", "w2"])
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


def test_review_run_detail_none_for_unscored_job(session, create_job) -> None:
    job = create_job(title="Fresh")

    assert get_review_run_detail(session, job_id=job.id) is None
