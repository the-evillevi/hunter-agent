"""Regression tests for the project-wide OpenAPI documentation policy.

The policy (HNTR-46): Swagger documents the JSON API surface only. Every
HTML/HTMX route — full pages, fragment reads, and fragment-returning
mutations — is excluded with ``include_in_schema=False``. Public operations
carry an intentional tag and a human-readable summary so /docs stays useful
as the API grows. New routes that break these rules should fail here, not in
a manual Swagger review.
"""

from fastapi.testclient import TestClient

from app.main import app


PUBLIC_PATHS = {
    "/health",
    "/api/companies/sp500/ingest",
}

APPROVED_TAGS = {"Monitoring", "Ingestion"}


def openapi_document() -> dict:
    """Build the schema without a database; it only inspects route metadata."""
    return app.openapi()


def test_schema_exposes_exactly_the_public_json_api() -> None:
    assert set(openapi_document()["paths"]) == PUBLIC_PATHS


def test_html_and_htmx_routes_are_excluded() -> None:
    documented_paths = set(openapi_document()["paths"])
    ui_paths = {
        route.path
        for route in app.routes
        if getattr(route, "include_in_schema", True) is False
    }

    # Spot-check that the exclusion list really covers the UI surfaces.
    for expected_ui_path in (
        "/",
        "/jobs",
        "/companies",
        "/profiles",
        "/jobs/partials/list",
        "/companies/partials/table",
        "/profiles/partials/list",
        "/sources/partials/list",
        "/applications/partials/list",
        "/applications/{application_id}",
        "/sources/{source_id}/toggle",
        "/companies/sp500/ingest",
    ):
        assert expected_ui_path in ui_paths
    assert documented_paths.isdisjoint(ui_paths)


def test_every_public_operation_has_an_approved_tag_and_a_summary() -> None:
    for path, operations in openapi_document()["paths"].items():
        for method, operation in operations.items():
            label = f"{method.upper()} {path}"
            tags = operation.get("tags", [])
            assert tags, f"{label} has no tag"
            assert set(tags) <= APPROVED_TAGS, f"{label} uses unapproved tags {tags}"
            assert operation.get("summary"), f"{label} has no summary"
            assert operation.get("description"), f"{label} has no description"


def test_public_operations_document_success_response_schemas() -> None:
    for path, operations in openapi_document()["paths"].items():
        for method, operation in operations.items():
            success = operation["responses"]["200"]
            schema = success["content"]["application/json"]["schema"]
            assert "$ref" in schema, f"{method.upper()} {path} returns untyped JSON"


def test_ingestion_trigger_documents_its_error_statuses() -> None:
    operation = openapi_document()["paths"]["/api/companies/sp500/ingest"]["post"]

    for status_code in ("409", "500", "502"):
        response = operation["responses"][status_code]
        assert response["description"], f"{status_code} has no description"
        schema = response["content"]["application/json"]["schema"]
        assert "$ref" in schema, f"{status_code} response is untyped"


def test_app_metadata_gives_swagger_users_project_context() -> None:
    info = openapi_document()["info"]

    assert info["title"] == "Hunter Agent"
    assert "JSON" in info["description"]
    assert info["version"] not in ("", "0.1")  # a real version, not a placeholder


def test_docs_and_openapi_endpoints_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
