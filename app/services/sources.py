"""Job source adapter protocol and registry.

Adapters receive a small explicit ``JobSourceRunContext`` instead of the full
application config. That keeps source implementations decoupled from unrelated
settings while still leaving room for credentials, profile hints, and search
parameters that a scraper run actually needs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol

from sqlmodel import Session, select

from app.models.source import Source


@dataclass(frozen=True)
class JobSourceIdentity:
    """Stable identity for a source adapter."""

    name: str
    display_name: str | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class JobSourceRunContext:
    """Minimal run input shared with source adapters."""

    keywords: tuple[str, ...] = ()
    location: str | None = None
    profile_id: int | None = None
    credentials: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedJobSource:
    """A source adapter resolved for a scraper run."""

    adapter: JobSourceAdapter
    db_source: Source | None = None


@dataclass(frozen=True)
class NormalizedJob:
    """Source-independent job shape ready for future persistence.

    The shape carries the existing ``jobs`` table fields where they are known,
    plus source identity, a stable identity hash, and raw metadata for debugging
    and future deduplication work.
    """

    title: str
    company: str
    location: str
    source: JobSourceIdentity
    url: str | None = None
    description: str | None = None
    external_id: str | None = None
    hash: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)
    scraped_at: datetime = field(default_factory=datetime.now)
    profile_id: int | None = None
    company_id: int | None = None
    location_id: int | None = None
    source_id: int | None = None
    score: int | None = None
    score_reasoning: str | None = None

    @classmethod
    def from_source(
        cls,
        *,
        source: JobSourceIdentity,
        title: str,
        company: str,
        location: str,
        url: str | None = None,
        description: str | None = None,
        external_id: str | None = None,
        raw_metadata: Mapping[str, Any] | None = None,
        scraped_at: datetime | None = None,
        profile_id: int | None = None,
        score: int | None = None,
        score_reasoning: str | None = None,
    ) -> "NormalizedJob":
        identity_hash = make_job_identity_hash(
            source_name=source.name,
            external_id=external_id,
            url=url,
            title=title,
            company=company,
            location=location,
        )
        return cls(
            title=title,
            company=company,
            location=location,
            source=source,
            url=url,
            description=description,
            external_id=external_id,
            hash=identity_hash,
            raw_metadata=raw_metadata or {},
            scraped_at=scraped_at or datetime.now(),
            profile_id=profile_id,
            score=score,
            score_reasoning=score_reasoning,
        )

    @property
    def source_name(self) -> str:
        return self.source.name

    @property
    def source_display_name(self) -> str:
        return self.source.label

    @property
    def identity_hash(self) -> str | None:
        return self.hash

    def to_job_dict(self) -> dict[str, Any]:
        """Return a dict compatible with the legacy ``scrape_jobs`` result."""
        return {
            "profile_id": self.profile_id,
            "title": self.title,
            "company": self.company,
            "company_id": self.company_id,
            "location": self.location,
            "location_id": self.location_id,
            "url": self.url,
            "source": self.source_name,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "source_display_name": self.source_display_name,
            "description": self.description,
            "hash": self.hash,
            "identity_hash": self.identity_hash,
            "external_id": self.external_id,
            "scraped_at": self.scraped_at,
            "score": self.score,
            "score_reasoning": self.score_reasoning,
            "raw_metadata": self.raw_metadata,
        }


class JobSourceAdapter(Protocol):
    """Async source adapter contract.

    ``fetch`` returns raw source records. ``normalize`` converts each raw record
    into the shared ``NormalizedJob`` shape before any persistence step exists.
    """

    identity: JobSourceIdentity

    async def fetch(self, context: JobSourceRunContext) -> Sequence[Mapping[str, Any]]:
        """Fetch raw jobs for one source."""

    def normalize(
        self,
        raw_job: Mapping[str, Any],
        context: JobSourceRunContext,
    ) -> NormalizedJob:
        """Normalize one raw source record."""


class UnknownJobSourceError(ValueError):
    """Raised when a requested source has no registered adapter."""


class JobSourceRegistry:
    """Registry for known source adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, JobSourceAdapter] = {}

    def register(self, adapter: JobSourceAdapter) -> None:
        self._adapters[adapter.identity.name] = adapter

    def resolve_selected(self, source_names: Iterable[str]) -> list[ResolvedJobSource]:
        missing_names = [name for name in source_names if name not in self._adapters]
        if missing_names:
            names = ", ".join(sorted(missing_names))
            raise UnknownJobSourceError(
                f"no registered job source adapter for: {names}"
            )
        return [
            ResolvedJobSource(adapter=self._adapters[name]) for name in source_names
        ]

    def resolve_enabled(self, session: Session) -> list[ResolvedJobSource]:
        """Resolve adapters enabled by the database.

        Until HNTR-22 adds explicit enablement fields, a row in ``sources`` is
        the minimum viable signal that the source participates in a run.
        """
        db_sources = {
            source.name: source for source in session.exec(select(Source)).all()
        }
        return [
            ResolvedJobSource(adapter=adapter, db_source=db_sources[name])
            for name, adapter in self._adapters.items()
            if name in db_sources
        ]


def make_job_identity_hash(
    *,
    source_name: str,
    external_id: str | None = None,
    url: str | None = None,
    title: str | None = None,
    company: str | None = None,
    location: str | None = None,
) -> str:
    """Build a stable minimum identity hash for future dedupe."""
    normalized_source_name = source_name.strip().casefold()
    if external_id:
        identity_parts = [normalized_source_name, external_id.strip().casefold()]
    elif url:
        identity_parts = [normalized_source_name, url.strip().casefold()]
    else:
        identity_parts = [
            normalized_source_name,
            (title or "").strip().casefold(),
            (company or "").strip().casefold(),
            (location or "").strip().casefold(),
        ]
    return sha256("|".join(identity_parts).encode("utf-8")).hexdigest()


default_source_registry = JobSourceRegistry()
