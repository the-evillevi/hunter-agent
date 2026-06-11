"""S&P 500 rank and tier enrichment helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.company_sources import NormalizedCompanyConstituent


WEIGHT_DERIVED_RANK_STATUS = "weight_derived"
FALLBACK_SOURCE_ORDER_RANK_STATUS = "fallback_source_order"
UNAVAILABLE_RANK_STATUS = "unavailable"

MAG7_TICKERS = frozenset(
    {"AAPL", "MSFT", "NVDA", "AMZN", "GOOG", "GOOGL", "META", "TSLA"}
)
MAG7_NAME_MARKERS = (
    "APPLE",
    "MICROSOFT",
    "NVIDIA",
    "AMAZON",
    "ALPHABET",
    "META PLATFORMS",
    "TESLA",
)


@dataclass(frozen=True)
class EnrichedCompanyConstituent:
    """Normalized constituent plus rank/tier metadata for persistence."""

    constituent: NormalizedCompanyConstituent
    sp500_weight_rank: int | None
    sp500_tier: str | None
    sp500_rank_source: str | None
    sp500_rank_status: str

    @property
    def symbol(self) -> str:
        return self.constituent.symbol

    @property
    def name(self) -> str:
        return self.constituent.name

    def to_company_payload(self) -> dict[str, Any]:
        """Return HNTR-33 company fields with HNTR-36 rank enrichment."""
        payload = self.constituent.to_company_payload()
        payload.update(
            {
                "sp500_weight_rank": self.sp500_weight_rank,
                "sp500_tier": self.sp500_tier,
                "sp500_rank_source": self.sp500_rank_source,
                "sp500_rank_status": self.sp500_rank_status,
            }
        )
        return payload


def enrich_sp500_rank_and_tier(
    constituents: list[NormalizedCompanyConstituent],
    *,
    allow_fallback_rank: bool = False,
) -> list[EnrichedCompanyConstituent]:
    """Assign S&P rank/tier metadata without reading config or persistence.

    True rank comes from descending provider weight. If weight is unavailable,
    fallback source-order rank is opt-in and marked so downstream code never
    mistakes enrichment order for true S&P weight rank.
    """
    if all(constituent.weight is not None for constituent in constituents):
        ordered = sorted(
            constituents,
            key=lambda company: (-float(company.weight), company.order, company.symbol),
        )
        return [
            enrich_constituent(
                constituent,
                weight_rank=rank,
                rank_source=f"{constituent.source_name}:weight",
                rank_status=WEIGHT_DERIVED_RANK_STATUS,
            )
            for rank, constituent in enumerate(ordered, start=1)
        ]

    if allow_fallback_rank:
        ordered = sorted(
            constituents, key=lambda company: (company.order, company.symbol)
        )
        return [
            enrich_constituent(
                constituent,
                weight_rank=rank,
                rank_source=f"{constituent.source_name}:source_order",
                rank_status=FALLBACK_SOURCE_ORDER_RANK_STATUS,
            )
            for rank, constituent in enumerate(ordered, start=1)
        ]

    return [
        enrich_constituent(
            constituent,
            weight_rank=None,
            rank_source=None,
            rank_status=UNAVAILABLE_RANK_STATUS,
        )
        for constituent in constituents
    ]


def enrich_constituent(
    constituent: NormalizedCompanyConstituent,
    *,
    weight_rank: int | None,
    rank_source: str | None,
    rank_status: str,
) -> EnrichedCompanyConstituent:
    """Apply tier rules to one normalized constituent."""
    return EnrichedCompanyConstituent(
        constituent=constituent,
        sp500_weight_rank=weight_rank,
        sp500_tier=assign_sp500_tier(
            symbol=constituent.symbol,
            name=constituent.name,
            weight_rank=weight_rank,
        ),
        sp500_rank_source=rank_source,
        sp500_rank_status=rank_status,
    )


def assign_sp500_tier(*, symbol: str, name: str, weight_rank: int | None) -> str | None:
    """Return the single stored S&P 500 tier for a company."""
    if is_mag7(symbol=symbol, name=name):
        return "mag7"

    if weight_rank is None:
        return None
    if weight_rank <= 100:
        return "top100"
    if weight_rank <= 200:
        return "top200"
    if weight_rank <= 300:
        return "top300"
    if weight_rank <= 400:
        return "top400"
    if weight_rank <= 500:
        return "top500"
    return None


def is_mag7(*, symbol: str, name: str) -> bool:
    """Detect Magnificent Seven companies by stable ticker or company name."""
    normalized_symbol = symbol.strip().upper()
    if normalized_symbol in MAG7_TICKERS:
        return True

    normalized_name = name.strip().upper()
    return any(marker in normalized_name for marker in MAG7_NAME_MARKERS)
