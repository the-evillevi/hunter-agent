"""Source lookup and enablement helpers.

The database is the runtime source of truth for whether a job source is enabled.
Config can still provide credentials, but the app should not require a config
edit just to toggle a source during normal use.
"""

from sqlmodel import Session, select

from app.models.source import Source


class SourceNotFoundError(ValueError):
    """Raised when a source toggle targets an unknown source."""


def list_sources(session: Session) -> list[Source]:
    """Return persisted job sources in a stable display order."""
    return list(session.exec(select(Source).order_by(Source.name)))


def get_source(session: Session, source_id: int) -> Source:
    """Return one source or raise a small domain error."""
    source = session.get(Source, source_id)
    if source is None:
        raise SourceNotFoundError(f"source {source_id} was not found")
    return source


def set_source_enabled(session: Session, source_id: int, enabled: bool) -> Source:
    """Persist the enabled flag for a source and return the updated row."""
    source = get_source(session, source_id)
    source.enabled = enabled
    session.add(source)
    session.commit()
    session.refresh(source)
    return source
