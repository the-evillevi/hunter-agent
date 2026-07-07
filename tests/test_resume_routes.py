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
