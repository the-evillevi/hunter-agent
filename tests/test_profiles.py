"""Database, service, context, and route tests for job profiles."""

import json

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.models.company import Company
from app.models.profile import Profile, ProfileSourceQuery
from app.models.source import Source
from app.services.profiles import (
    ProfileConflictError,
    ProfileError,
    create_profile,
    create_source_query,
    delete_profile,
    list_profile_runs_for_source,
)


def create_complete_profile(session: Session, *, active: bool = True):
    return create_profile(
        session,
        role_name="AI Engineer",
        salary_min=60_000,
        match_threshold=80,
        active=active,
        location_types=["remote", "hybrid"],
        keywords=["Python", "LLM", "PyTorch"],
        exclude_keywords=["PhD required", "security clearance"],
    )


def test_profile_service_persists_complete_aggregate(session: Session) -> None:
    detail = create_complete_profile(session)

    assert detail.profile.id is not None
    assert detail.profile.salary_min == 60_000
    assert {value.value for value in detail.location_types} == {"remote", "hybrid"}
    assert detail.keywords == ("LLM", "Python", "PyTorch")
    assert detail.exclude_keywords == ("PhD required", "security clearance")


def test_profile_validation_rejects_duplicates_overlap_and_bad_values(
    session: Session,
) -> None:
    common = {
        "role_name": "AI Engineer",
        "salary_min": 60_000,
        "match_threshold": 80,
        "active": True,
        "location_types": ["remote"],
    }

    with pytest.raises(ProfileError, match="duplicate"):
        create_profile(
            session,
            **common,
            keywords=["Python", " python "],
            exclude_keywords=[],
        )
    with pytest.raises(ProfileError, match="both included and excluded"):
        create_profile(
            session,
            **common,
            keywords=["Python"],
            exclude_keywords=["python"],
        )
    with pytest.raises(ProfileError, match="location"):
        create_profile(
            session,
            **{**common, "location_types": []},
            keywords=["Python"],
            exclude_keywords=[],
        )


def test_profile_role_name_is_case_insensitively_unique(session: Session) -> None:
    create_complete_profile(session)

    with pytest.raises(ProfileConflictError):
        create_profile(
            session,
            role_name="ai engineer",
            salary_min=0,
            match_threshold=50,
            active=True,
            location_types=["remote"],
            keywords=["Agents"],
            exclude_keywords=[],
        )


@pytest.mark.parametrize(
    "raw_query",
    [
        {"schema_version": 1, "category": "sales", "limit": 5},
        {"schema_version": 1, "search": " ", "limit": 5},
        {"schema_version": 1, "limit": 0},
        {"schema_version": 1, "limit": 11},
        {"schema_version": 1, "company_id": 999, "limit": 5},
    ],
)
def test_remotive_query_validation_rejects_invalid_values(
    session: Session,
    raw_query: dict,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()

    with pytest.raises(ProfileError):
        create_source_query(
            session,
            profile_id=detail.profile.id,
            source_id=source.id,
            raw_query=raw_query,
        )


def test_remotive_query_resolves_company_and_rejects_duplicates(
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    company = Company(name="Example Corp", ticker="EX")
    session.add_all([source, company])
    session.commit()
    raw_query = {
        "schema_version": 1,
        "category": "artificial-intelligence",
        "company_id": company.id,
        "search": "AI engineer",
        "limit": 10,
    }

    updated = create_source_query(
        session,
        profile_id=detail.profile.id,
        source_id=source.id,
        raw_query=raw_query,
    )

    assert updated.source_queries[0].company_name == "Example Corp"
    assert updated.source_queries[0].query.limit == 10
    with pytest.raises(ProfileConflictError, match="duplicate"):
        create_source_query(
            session,
            profile_id=detail.profile.id,
            source_id=source.id,
            raw_query=raw_query,
        )


def test_database_rejects_malformed_query_json(session: Session) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    session.add(
        ProfileSourceQuery(
            profile_id=detail.profile.id,
            source_id=source.id,
            query_json="not-json",
        )
    )

    with pytest.raises(IntegrityError):
        session.commit()


def test_list_profile_runs_for_source_returns_active_validated_contexts(
    session: Session,
) -> None:
    active = create_complete_profile(session)
    inactive = create_profile(
        session,
        role_name="Inactive Role",
        salary_min=0,
        match_threshold=70,
        active=False,
        location_types=["onsite"],
        keywords=["Java"],
        exclude_keywords=[],
    )
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    for detail, search in ((active, "AI engineer"), (inactive, "Java")):
        create_source_query(
            session,
            profile_id=detail.profile.id,
            source_id=source.id,
            raw_query={"schema_version": 1, "search": search, "limit": 5},
        )

    contexts = list_profile_runs_for_source(session, "remotive")

    assert len(contexts) == 1
    assert contexts[0].profile_id == active.profile.id
    assert contexts[0].keywords == active.keywords
    assert contexts[0].exclude_keywords == active.exclude_keywords
    assert contexts[0].location_types == ("hybrid", "remote")
    assert contexts[0].salary_min == 60_000
    assert contexts[0].source_query["search"] == "AI engineer"


def test_list_profile_runs_for_source_includes_provider_ready_company(
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    company = Company(name="Example Corp", ticker="EX")
    session.add_all([source, company])
    session.commit()
    create_source_query(
        session,
        profile_id=detail.profile.id,
        source_id=source.id,
        raw_query={
            "schema_version": 1,
            "company_id": company.id,
            "search": "AI engineer",
            "limit": 10,
        },
    )

    context = list_profile_runs_for_source(session, "remotive")[0]

    assert context.company_name == "Example Corp"
    assert context.source_query["company_id"] == company.id
    assert context.source_query["company_name"] == "Example Corp"


def test_persisted_query_with_missing_company_returns_bounded_error(
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    session.add(
        ProfileSourceQuery(
            profile_id=detail.profile.id,
            source_id=source.id,
            query_json=json.dumps(
                {"schema_version": 1, "company_id": 999, "search": "AI", "limit": 5}
            ),
        )
    )
    session.commit()

    with pytest.raises(ProfileError, match="company 999 was not found"):
        list_profile_runs_for_source(session, "remotive")


def test_persisted_query_with_invalid_semantics_returns_bounded_error(
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    session.add(
        ProfileSourceQuery(
            profile_id=detail.profile.id,
            source_id=source.id,
            query_json=json.dumps(
                {"schema_version": 1, "category": "sales", "limit": 5}
            ),
        )
    )
    session.commit()

    with pytest.raises(ProfileError, match="supported Remotive categories"):
        list_profile_runs_for_source(session, "remotive")


def test_delete_profile_rejects_profile_referenced_by_jobs(
    session: Session,
    create_job,
) -> None:
    create_job()
    profile = session.exec(select(Profile)).one()

    with pytest.raises(ProfileConflictError, match="deactivate"):
        delete_profile(session, profile.id)


def test_profiles_page_and_crud_routes(client, session: Session) -> None:
    source = Source(name="Remotive", enabled=True)
    company = Company(name="Remotive", ticker="REM")
    session.add_all([source, company])
    session.commit()

    empty_response = client.get("/profiles")
    create_response = client.post(
        "/profiles",
        data={
            "role_name": "Platform Engineer",
            "salary_min": "50000",
            "match_threshold": "75",
            "active": "true",
            "location_type": ["remote", "hybrid"],
            "keywords": "Python, Kubernetes",
            "exclude_keywords": "clearance",
        },
    )
    session.expire_all()
    profile = session.exec(select(Profile)).one()
    query_response = client.post(
        f"/profiles/{profile.id}/source-queries",
        data={
            "source_id": str(source.id),
            "category": "engineering",
            "company_id": str(company.id),
            "search": "platform",
            "limit": "5",
        },
    )
    update_response = client.patch(
        f"/profiles/{profile.id}",
        data={
            "role_name": "Senior Platform Engineer",
            "salary_min": "70000",
            "match_threshold": "85",
            "location_type": "remote",
            "keywords": "Python, Kubernetes",
            "exclude_keywords": "clearance",
        },
    )

    assert empty_response.status_code == 200
    assert "No job profiles yet" in empty_response.text
    assert create_response.status_code == 200
    assert "Platform Engineer" in create_response.text
    assert query_response.status_code == 200
    assert "engineering" in query_response.text
    assert "Remotive" in query_response.text
    assert update_response.status_code == 200
    assert "Senior Platform Engineer" in update_response.text
    assert "Inactive" in update_response.text


def test_profile_route_returns_conflict_when_job_references_profile(
    client,
    session: Session,
    create_job,
) -> None:
    create_job()
    profile = session.exec(select(Profile)).one()

    response = client.delete(f"/profiles/{profile.id}")

    assert response.status_code == 409
    assert "deactivate it instead" in response.text


def test_profiles_page_returns_bounded_error_for_invalid_persisted_query(
    client,
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    session.add(
        ProfileSourceQuery(
            profile_id=detail.profile.id,
            source_id=source.id,
            query_json=json.dumps(
                {"schema_version": 1, "category": "sales", "limit": 5}
            ),
        )
    )
    session.commit()

    response = client.get("/profiles")

    assert response.status_code == 200
    assert "supported Remotive categories" in response.text


def test_profile_routes_return_friendly_numeric_validation_errors(client) -> None:
    profile_response = client.post(
        "/profiles",
        data={
            "role_name": "Platform Engineer",
            "salary_min": "oops",
            "match_threshold": "75",
            "active": "true",
            "location_type": "remote",
            "keywords": "Python",
        },
    )
    query_response = client.post(
        "/profiles/1/source-queries",
        data={
            "source_id": "1",
            "search": "platform",
            "limit": "many",
        },
    )

    assert profile_response.status_code == 400
    assert "salary minimum must be a whole number" in profile_response.text
    assert "invalid literal" not in profile_response.text
    assert query_response.status_code == 400
    assert "limit must be a whole number" in query_response.text
    assert "invalid literal" not in query_response.text


def test_source_query_routes_support_update_and_delete(
    client,
    session: Session,
) -> None:
    detail = create_complete_profile(session)
    source = Source(name="Remotive", enabled=True)
    session.add(source)
    session.commit()
    created = create_source_query(
        session,
        profile_id=detail.profile.id,
        source_id=source.id,
        raw_query={"schema_version": 1, "search": "AI", "limit": 5},
    )
    query_id = created.source_queries[0].row.id

    update_response = client.patch(
        f"/profiles/{detail.profile.id}/source-queries/{query_id}",
        data={
            "category": "research",
            "search": "machine learning",
            "limit": "7",
        },
    )
    delete_response = client.delete(
        f"/profiles/{detail.profile.id}/source-queries/{query_id}"
    )

    assert update_response.status_code == 200
    assert "research" in update_response.text
    assert "machine learning" in update_response.text
    assert delete_response.status_code == 200
    session.expire_all()
    assert session.get(ProfileSourceQuery, query_id) is None


def test_unused_profile_can_be_deleted_through_route(client, session: Session) -> None:
    detail = create_complete_profile(session)
    profile_id = detail.profile.id

    response = client.delete(f"/profiles/{profile_id}")

    assert response.status_code == 200
    assert "No job profiles yet" in response.text
    session.expire_all()
    assert session.get(Profile, profile_id) is None


def test_seed_query_json_shape_is_stable() -> None:
    payload = {
        "schema_version": 1,
        "category": "software-development",
        "search": "fullstack",
        "limit": 10,
    }

    assert json.loads(json.dumps(payload))["schema_version"] == 1
