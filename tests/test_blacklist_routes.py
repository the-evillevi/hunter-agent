"""Route tests for the blacklist HTMX endpoints (HNTR-52)."""

from app.main import app
from app.services.blacklist import add_company_to_blacklist, add_job_to_blacklist


def test_blacklist_job_re_renders_the_row(client, session, create_job) -> None:
    job = create_job(title="Spam role")

    response = client.post(f"/jobs/{job.id}/blacklist", data={"reason": "spam"})

    assert response.status_code == 200
    assert f'id="job-row-{job.id}"' in response.text
    assert "Blacklisted (job)" in response.text
    assert 'title="spam"' in response.text


def test_blacklist_unknown_job_is_404(client) -> None:
    assert client.post("/jobs/999/blacklist").status_code == 404


def test_duplicate_job_blacklist_is_409_with_row_fragment(
    client, session, create_job
) -> None:
    job = create_job()
    add_job_to_blacklist(session, job_id=job.id)

    response = client.post(f"/jobs/{job.id}/blacklist")

    assert response.status_code == 409
    assert "already blacklisted" in response.text
    assert f'id="job-row-{job.id}"' in response.text


def test_unblacklist_job_deletes_and_re_renders(client, session, create_job) -> None:
    job = create_job()
    add_job_to_blacklist(session, job_id=job.id)

    response = client.delete(f"/jobs/{job.id}/blacklist")

    assert response.status_code == 200
    assert "Blacklisted (job)" not in response.text


def test_unblacklist_job_without_entry_is_409(client, session, create_job) -> None:
    job = create_job()

    response = client.delete(f"/jobs/{job.id}/blacklist")

    assert response.status_code == 409
    assert "no blacklist entry" in response.text


def test_blacklist_company_re_renders_the_list(client, session, create_job) -> None:
    first = create_job(title="Role one", company_name="Blocked Corp")
    create_job(title="Role two", company_name="Blocked Corp")

    response = client.post(
        f"/companies/{first.company_id}/blacklist", data={"reason": "culture"}
    )

    assert response.status_code == 200
    # Both rows of the company show the state after one mutation.
    assert response.text.count("Blacklisted (company)") == 2


def test_blacklist_unknown_company_is_404(client) -> None:
    assert client.post("/companies/999/blacklist").status_code == 404


def test_duplicate_company_blacklist_is_409_with_banner(
    client, session, create_job
) -> None:
    job = create_job(company_name="Blocked Corp")
    add_company_to_blacklist(session, company_id=job.company_id)

    response = client.post(f"/companies/{job.company_id}/blacklist")

    assert response.status_code == 409
    assert "already blacklisted" in response.text


def test_unblacklist_company_restores_the_rows(client, session, create_job) -> None:
    job = create_job(company_name="Blocked Corp")
    add_company_to_blacklist(session, company_id=job.company_id)

    response = client.delete(f"/companies/{job.company_id}/blacklist")

    assert response.status_code == 200
    assert "Blacklisted (company)" not in response.text


def test_jobs_list_shows_blacklist_state(client, session, create_job) -> None:
    job = create_job(title="Blocked role")
    add_job_to_blacklist(session, job_id=job.id, reason="scam")

    response = client.get("/jobs/partials/list")

    assert response.status_code == 200
    assert "Blacklisted (job)" in response.text


def test_blacklist_routes_are_excluded_from_openapi() -> None:
    paths = app.openapi()["paths"]
    assert "/jobs/{job_id}/blacklist" not in paths
    assert "/companies/{company_id}/blacklist" not in paths
