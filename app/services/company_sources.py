"""Company source adapter protocol and registry.

This mirrors the job-source boundary from HNTR-20 without introducing a deeper
generic source framework. Company ingestion has different normalized output and
provider concerns, so a small company-specific protocol is easier to read.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.models.config import AppConfig


@dataclass(frozen=True)
class CompanySourceIdentity:
    """Stable identity for a company-source adapter."""

    name: str
    display_name: str | None = None

    @property
    def label(self) -> str:
        return self.display_name or self.name


@dataclass(frozen=True)
class NormalizedCompanyConstituent:
    """Source-independent S&P constituent shape before persistence.

    The SSGA/SPY holdings workbook provides the first required fields:
    ``symbol``, ``name``, ``weight``, and ``order``. Extra provider metadata is
    carried forward so HNTR-35 and later upsert work can map it into HNTR-33's
    company schema without re-fetching or reparsing source rows.
    """

    symbol: str
    name: str
    weight: float | None
    order: int
    source: CompanySourceIdentity
    identifier: str | None = None
    sedol: str | None = None
    sector: str | None = None
    shares_held: float | None = None
    local_currency: str | None = None
    raw_metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_source(
        cls,
        *,
        source: CompanySourceIdentity,
        symbol: str,
        name: str,
        weight: float | None,
        order: int,
        identifier: str | None = None,
        sedol: str | None = None,
        sector: str | None = None,
        shares_held: float | None = None,
        local_currency: str | None = None,
        raw_metadata: Mapping[str, Any] | None = None,
    ) -> "NormalizedCompanyConstituent":
        """Build a normalized constituent from one provider row."""
        return cls(
            symbol=symbol.strip().upper(),
            name=name.strip(),
            weight=weight,
            order=order,
            source=source,
            identifier=identifier,
            sedol=sedol,
            sector=sector,
            shares_held=shares_held,
            local_currency=local_currency,
            raw_metadata=raw_metadata or {},
        )

    @property
    def source_name(self) -> str:
        return self.source.name

    @property
    def source_display_name(self) -> str:
        return self.source.label

    def to_company_payload(self) -> dict[str, Any]:
        """Return a dict compatible with HNTR-33 company field names."""
        return {
            "name": self.name,
            "ticker": self.symbol,
            "sp500_source": self.source_name,
            "sp500_provider": self.source_name,
            "sp500_identifier": self.identifier,
            "sp500_sedol": self.sedol,
            "sp500_weight": self.weight,
            "sp500_shares_held": self.shares_held,
            "sp500_local_currency": self.local_currency,
            "is_sp500": True,
            "raw_metadata": self.raw_metadata,
        }


class CompanySourceAdapter(Protocol):
    """Async company-source adapter contract.

    Adapters receive the full validated ``AppConfig`` so concrete providers can
    use existing config sections without another validation layer.
    """

    identity: CompanySourceIdentity

    async def fetch(self, config: AppConfig) -> Sequence[Mapping[str, Any]]:
        """Fetch raw company rows for one source."""

    def normalize(
        self,
        raw_company: Mapping[str, Any],
        config: AppConfig,
    ) -> NormalizedCompanyConstituent:
        """Normalize one raw provider row."""


class CompanySourceRegistry:
    """Registry for company-source adapters."""

    def __init__(self) -> None:
        self._adapters: dict[str, CompanySourceAdapter] = {}

    def register(self, adapter: CompanySourceAdapter) -> None:
        self._adapters[adapter.identity.name] = adapter

    def resolve_selected(
        self,
        source_names: Iterable[str],
    ) -> list[CompanySourceAdapter]:
        """Resolve adapters from an explicit source list in the given order."""
        return [self._adapters[name] for name in source_names if name in self._adapters]


async def fetch_company_constituents(
    *,
    registry: CompanySourceRegistry,
    source_names: Iterable[str],
    config: AppConfig,
) -> list[NormalizedCompanyConstituent]:
    """Fetch and normalize companies without writing to persistence."""
    normalized_companies: list[NormalizedCompanyConstituent] = []
    for adapter in registry.resolve_selected(source_names):
        raw_companies = await adapter.fetch(config)
        normalized_companies.extend(
            adapter.normalize(raw_company, config) for raw_company in raw_companies
        )
    return normalized_companies


default_company_source_registry = CompanySourceRegistry()
