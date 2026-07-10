"""Route and template tests for the review queue (HNTR-2)."""

import json

from sqlmodel import Session, select

from app.main import app
from app.models.application import Application, ApplicationStatus
from app.models.score_run import ScoreLayerResultRow, ScoreRun
from app.services.blacklist import add_job_to_blacklist


def test_review_page_renders_with_filters(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Reviewable role")
    create_score_run(job, score=77)

    response = client.get("/review")

    assert response.status_code == 200
    assert "Review queue" in response.text
    assert "Reviewable role" in response.text
    assert "77/100" in response.text


def test_table_partial_preserves_filters_in_pagination_urls(
    client, session, create_job, create_score_run
) -> None:
    for index in range(3):
        job = create_job(title=f"Role {index}")
        create_score_run(job, score=60 + index)

    response = client.get(
        "/review/partials/table",
        params={
            "profile_id": job.profile_id,
            "min_score": 50,
            "page": 1,
            "page_size": 2,
        },
    )

    assert response.status_code == 200
    assert "min_score=50" in response.text
    assert f"profile_id={job.profile_id}" in response.text
    assert "page=2" in response.text


def test_blank_filter_values_mean_no_filter(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Unfiltered role")
    create_score_run(job, score=10)

    response = client.get(
        "/review/partials/table", params={"profile_id": "", "min_score": ""}
    )

    assert response.status_code == 200
    assert "Unfiltered role" in response.text


def test_garbage_filter_values_are_400_html_fragments(client) -> None:
    response = client.get("/review/partials/table", params={"min_score": "many"})

    assert response.status_code == 400
    # HTML, not JSON: the htmx error hook only swaps text/html responses.
    assert "text/html" in response.headers["content-type"]
    assert "must be a number" in response.text

    out_of_range = client.get("/review", params={"min_score": "200"})
    assert out_of_range.status_code == 400
    assert "out of range" in out_of_range.text


def test_detail_partial_shows_layers_warnings_and_version(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Evidence role")
    run = create_score_run(job, score=82)
    run.warnings = json.dumps(["unchecked constraint: salary"])
    session.add(run)
    session.add(
        ScoreLayerResultRow(
            score_run_id=run.id,
            layer="keyword",
            status="success",
            algorithm_version="1",
            score=82,
            explanation="matched python, mlops",
        )
    )
    session.commit()

    response = client.get(f"/review/partials/jobs/{job.id}/detail")

    assert response.status_code == 200
    assert "matched python, mlops" in response.text
    assert "unchecked constraint: salary" in response.text
    assert "pipeline v1" in response.text


def test_detail_partial_for_missing_job_is_404(client) -> None:
    assert client.get("/review/partials/jobs/999/detail").status_code == 404


def test_detail_partial_for_unscored_job_shows_empty_state(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Never scored")

    response = client.get(f"/review/partials/jobs/{job.id}/detail")

    assert response.status_code == 200
    assert "never been scored" in response.text


def test_draft_action_creates_draft_and_flips_row_state(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Draft me")
    create_score_run(job, score=91)

    response = client.post(f"/jobs/{job.id}/application")

    assert response.status_code == 200
    assert "drafted" in response.text
    assert "Start draft" not in response.text
    application = session.exec(
        select(Application).where(Application.job_id == job.id)
    ).first()
    assert application is not None
    assert application.status == ApplicationStatus.draft


def test_draft_action_rejects_blacklisted_job_with_409_row(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Blocked role")
    create_score_run(job, score=95)
    add_job_to_blacklist(session, job_id=job.id, reason="scam")

    response = client.post(f"/jobs/{job.id}/application")

    assert response.status_code == 409
    assert "blacklisted" in response.text


def test_draft_action_rejects_duplicates_with_409_row(
    client, session, create_job, create_score_run
) -> None:
    job = create_job(title="Already drafted")
    create_score_run(job, score=95)
    assert client.post(f"/jobs/{job.id}/application").status_code == 200

    response = client.post(f"/jobs/{job.id}/application")

    assert response.status_code == 409
    assert "already has" in response.text


def test_draft_action_missing_job_is_404(client) -> None:
    assert client.post("/jobs/999/application").status_code == 404


def test_empty_state_renders(client) -> None:
    response = client.get("/review")

    assert response.status_code == 200
    assert "No jobs match these filters" in response.text


def test_review_routes_are_excluded_from_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/review" not in paths
    assert "/review/partials/table" not in paths
    assert "/review/partials/jobs/{job_id}/detail" not in paths
    assert "/jobs/{job_id}/application" not in paths
