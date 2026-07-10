"""HTTP tests for the resume management routes."""

from pathlib import Path

import pytest
from sqlmodel import Session

from app.routes import resumes as resumes_route
from app.services.ollama_client import ScoringResult
from app.services.resume_import import import_resume, load_resume_document
from app.services.resume_tailor import ResumeTailor


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "resume_sample.json"


class FakeScoringClient:
    """Deterministic scorer so route tests never touch a real model."""

    model_name = "fake-model"
    prompt_version = "test"

    def score_item(
        self, *, item_content: str, job_title: str, job_description: str
    ) -> ScoringResult:
        score = 90 if "IBM" in item_content else 20
        return ScoringResult(score=score, reasoning="Route test judgement.")


@pytest.fixture()
def imported_resume_id(session: Session) -> int:
    document = load_resume_document(FIXTURE_PATH)
    return import_resume(session, document).id


@pytest.fixture()
def fake_tailor(monkeypatch):
    """Replace the route's ResumeTailor with one using the fake scorer."""

    def build_fake_tailor() -> ResumeTailor:
        return ResumeTailor(client=FakeScoringClient())

    monkeypatch.setattr(resumes_route, "ResumeTailor", build_fake_tailor)


def test_resumes_page_lists_imported_resume(client, imported_resume_id) -> None:
    response = client.get("/resumes")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "sample" in response.text
    assert "master" in response.text


def test_resumes_page_renders_empty_state(client) -> None:
    response = client.get("/resumes")

    assert response.status_code == 200
    assert "No resumes yet" in response.text


def test_resumes_list_partial_returns_fragment(client, imported_resume_id) -> None:
    response = client.get("/resumes/partials/list")

    assert response.status_code == 200
    assert "<table" in response.text
    assert "<html" not in response.text  # fragment, not a full page


def test_resume_detail_page_shows_sections_and_export_links(
    client, imported_resume_id
) -> None:
    response = client.get(f"/resumes/{imported_resume_id}")

    assert response.status_code == 200
    assert "Work Experience" in response.text
    assert "Sample Person" in response.text
    assert f"/resumes/{imported_resume_id}/export?format=pdf" in response.text
    assert "Tailor for a job" in response.text  # masters offer tailoring


def test_resume_detail_returns_404_for_missing_resume(client) -> None:
    response = client.get("/resumes/999")

    assert response.status_code == 404


def test_tailor_route_creates_variant_and_renders_result(
    client, imported_resume_id, create_job, fake_tailor
) -> None:
    job = create_job(title="Data Engineer")

    response = client.post(
        f"/resumes/{imported_resume_id}/tailor",
        content=f"job_id={job.id}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert "Created" in response.text
    assert "tailored-data-engineer-" in response.text
    assert "survived the relevance filter" in response.text


def test_tailor_route_rejects_missing_job_id(
    client, imported_resume_id, fake_tailor
) -> None:
    response = client.post(
        f"/resumes/{imported_resume_id}/tailor",
        content="",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 400


def test_tailor_route_returns_404_for_missing_job(
    client, imported_resume_id, fake_tailor
) -> None:
    response = client.post(
        f"/resumes/{imported_resume_id}/tailor",
        content="job_id=999",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 404


def test_export_json_downloads_custom_schema(client, imported_resume_id) -> None:
    response = client.get(f"/resumes/{imported_resume_id}/export?format=json")

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    payload = response.json()
    assert payload["name"] == "sample"
    assert payload["sections"]


def test_export_json_resume_maps_standard_schema(client, imported_resume_id) -> None:
    response = client.get(f"/resumes/{imported_resume_id}/export?format=json_resume")

    assert response.status_code == 200
    document = response.json()
    assert document["basics"]["name"] == "Sample Person"
    assert document["work"]


def test_export_html_renders_inline_preview(client, imported_resume_id) -> None:
    response = client.get(f"/resumes/{imported_resume_id}/export?format=html")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "content-disposition" not in response.headers
    assert "Sample Person" in response.text


def test_export_rejects_unknown_format(client, imported_resume_id) -> None:
    response = client.get(f"/resumes/{imported_resume_id}/export?format=docx")

    assert response.status_code == 400


def test_export_returns_404_for_missing_resume(client) -> None:
    response = client.get("/resumes/999/export?format=json")

    assert response.status_code == 404


def test_create_resume_from_full_document(client) -> None:
    document = {
        "name": "posted",
        "basics": {"name": "Posted Person", "email": "posted@example.test"},
        "sections": [
            {
                "type": "experience",
                "title": "Work Experience",
                "items": [{"position": "Engineer", "company": "IBM"}],
            }
        ],
    }

    response = client.post("/resumes", json=document)

    assert response.status_code == 201
    created = response.json()
    assert response.headers["location"] == f"/resumes/{created['id']}"

    detail_page = client.get(f"/resumes/{created['id']}")
    assert detail_page.status_code == 200
    assert "Posted Person" in detail_page.text


def test_create_resume_with_name_only(client) -> None:
    response = client.post("/resumes", json={"name": "scratch"})

    assert response.status_code == 201
    assert response.json()["name"] == "scratch"
    assert client.get(f"/resumes/{response.json()['id']}").status_code == 200


def test_create_resume_rejects_invalid_document(client) -> None:
    bad_document = {
        "name": "bad",
        "basics": {},
        "sections": [{"type": "hobbies", "title": "Hobbies", "items": []}],
    }

    assert client.post("/resumes", json=bad_document).status_code == 422
    assert client.post("/resumes", json={}).status_code == 422
    assert client.post("/resumes", content="not json").status_code == 422


def test_patch_resume_renames_profile(client, imported_resume_id) -> None:
    response = client.patch(
        f"/resumes/{imported_resume_id}", json={"name": "renamed"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "renamed"
    assert "renamed" in client.get(f"/resumes/{imported_resume_id}").text


def test_patch_resume_edits_item_and_section_order(
    client, session, imported_resume_id
) -> None:
    from app.services.resume_crud import get_resume_detail

    detail = get_resume_detail(session, imported_resume_id)
    experience = next(
        section for section in detail.sections if section.section_type == "experience"
    )
    item = experience.items[0]

    response = client.patch(
        f"/resumes/{imported_resume_id}",
        json={
            "items": [
                {
                    "id": item.id,
                    "content": {"position": "Staff Engineer", "company": "IBM"},
                    "order_idx": 5,
                }
            ],
            "sections": [{"id": experience.id, "order_idx": 9}],
        },
    )

    assert response.status_code == 200
    updated = get_resume_detail(session, imported_resume_id)
    updated_experience = next(
        section for section in updated.sections if section.id == experience.id
    )
    assert updated_experience.order_idx == 9
    updated_item = next(
        entry for entry in updated_experience.items if entry.id == item.id
    )
    assert updated_item.content["position"] == "Staff Engineer"


def test_patch_resume_returns_404_for_missing_profile(client) -> None:
    assert client.patch("/resumes/999", json={"name": "x"}).status_code == 404


def test_patch_resume_rejects_foreign_item(client, session) -> None:
    """Editing an item through another profile's PATCH must fail."""
    document = load_resume_document(FIXTURE_PATH)
    first = import_resume(session, document)
    second = import_resume(session, document)

    from app.services.resume_crud import get_resume_detail

    first_item = get_resume_detail(session, first.id).sections[0].items[0]

    response = client.patch(
        f"/resumes/{second.id}",
        json={"items": [{"id": first_item.id, "order_idx": 1}]},
    )

    assert response.status_code == 404


def test_job_tailor_route_creates_variant(
    client, imported_resume_id, create_job, fake_tailor
) -> None:
    job = create_job(title="Data Engineer")

    response = client.post(
        f"/jobs/{job.id}/tailor",
        content=f"resume_id={imported_resume_id}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert "Created" in response.text
    assert "tailored-data-engineer-" in response.text


def test_jobs_list_offers_tailor_control(
    client, imported_resume_id, create_job
) -> None:
    job = create_job(title="Data Engineer")

    response = client.get("/jobs/partials/list")

    assert response.status_code == 200
    assert f'hx-post="/jobs/{job.id}/tailor"' in response.text
    assert 'name="resume_id"' in response.text


def test_application_card_links_tailored_resume(
    client, session, imported_resume_id, create_job, create_application, fake_tailor
) -> None:
    job = create_job(title="Data Engineer")
    create_application(job=job)

    without_resume = client.get("/applications/partials/list")
    assert "Export:" not in without_resume.text

    client.post(
        f"/resumes/{imported_resume_id}/tailor",
        content=f"job_id={job.id}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    with_resume = client.get("/applications/partials/list")
    assert "tailored-data-engineer-" in with_resume.text
    assert "/export?format=pdf" in with_resume.text


def test_dashboard_shows_recent_tailor_runs_card(
    client, imported_resume_id, create_job, fake_tailor
) -> None:
    job = create_job(title="Data Engineer")

    empty_home = client.get("/")
    assert "Recently tailored resumes" in empty_home.text
    assert "No tailoring runs yet" in empty_home.text

    client.post(
        f"/resumes/{imported_resume_id}/tailor",
        content=f"job_id={job.id}",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    home = client.get("/")
    assert "tailored-data-engineer-" in home.text
    assert 'hx-get="/resumes/partials/recent"' in home.text

    partial = client.get("/resumes/partials/recent")
    assert partial.status_code == 200
    assert "tailored-data-engineer-" in partial.text
    assert "<html" not in partial.text  # fragment, not a full page
