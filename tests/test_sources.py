"""Tests for persisted source enablement."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine, select

from app.db.database import get_session
from app.main import app
from app.models.source import Source
from app.services.sources import SourceNotFoundError, list_sources, set_source_enabled


@pytest.fixture()
def source_engine(tmp_path) -> Iterator[Engine]:
    engine = create_engine(f"sqlite:///{tmp_path / 'sources.db'}")
    Source.__table__.create(engine)
    try:
        yield engine
    finally:
        Source.__table__.drop(engine)
        engine.dispose()


@pytest.fixture()
def source_session(source_engine: Engine) -> Iterator[Session]:
    with Session(source_engine) as session:
        yield session


@pytest.fixture()
def source_client(source_engine: Engine) -> Iterator[TestClient]:
    def override_get_session() -> Iterator[Session]:
        with Session(source_engine) as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_source_enablement_defaults_to_enabled(source_session: Session) -> None:
    source = Source(name="Adzuna")
    source_session.add(source)
    source_session.commit()

    saved_source = source_session.exec(select(Source)).one()

    assert saved_source.enabled is True


def test_list_sources_returns_display_names_in_stable_order(
    source_session: Session,
) -> None:
    source_session.add(Source(name="Remotive", enabled=True))
    source_session.add(Source(name="Adzuna", enabled=False))
    source_session.commit()

    sources = list_sources(source_session)

    assert [(source.name, source.enabled) for source in sources] == [
        ("Adzuna", False),
        ("Remotive", True),
    ]


def test_set_source_enabled_persists_toggle(source_session: Session) -> None:
    source = Source(name="Remotive", enabled=True)
    source_session.add(source)
    source_session.commit()

    updated_source = set_source_enabled(source_session, source.id, False)

    assert updated_source.enabled is False
    assert source_session.get(Source, source.id).enabled is False


def test_set_source_enabled_rejects_unknown_source(source_session: Session) -> None:
    with pytest.raises(SourceNotFoundError):
        set_source_enabled(source_session, 404, True)


def test_sources_partial_renders_source_status(
    source_client: TestClient,
    source_session: Session,
) -> None:
    source_session.add(Source(name="Adzuna", enabled=True))
    source_session.commit()

    response = source_client.get("/sources/partials/list")

    assert response.status_code == 200
    assert "Adzuna" in response.text
    assert "Enabled" in response.text


def test_source_toggle_route_updates_database_and_returns_panel(
    source_client: TestClient,
    source_session: Session,
) -> None:
    source = Source(name="Remotive", enabled=True)
    source_session.add(source)
    source_session.commit()

    response = source_client.post(f"/sources/{source.id}/toggle?enabled=false")

    assert response.status_code == 200
    assert "Remotive" in response.text
    assert "Disabled" in response.text
    source_session.expire_all()
    assert source_session.get(Source, source.id).enabled is False


def test_source_toggle_route_returns_404_for_missing_source(
    source_client: TestClient,
) -> None:
    response = source_client.post("/sources/404/toggle?enabled=true")

    assert response.status_code == 404
