"""Tests for S&P 500 rank and tier enrichment."""

from __future__ import annotations

from app.services.company_sources import (
    CompanySourceIdentity,
    NormalizedCompanyConstituent,
)
from app.services.sp500_enrichment import (
    FALLBACK_SOURCE_ORDER_RANK_STATUS,
    UNAVAILABLE_RANK_STATUS,
    WEIGHT_DERIVED_RANK_STATUS,
    assign_sp500_tier,
    enrich_sp500_rank_and_tier,
)


SSGA = CompanySourceIdentity(name="ssga_spy_holdings", display_name="SSGA SPY")
WIKIPEDIA = CompanySourceIdentity(
    name="wikipedia_sp500_enrichment",
    display_name="Wikipedia S&P 500 enrichment",
)


def constituent(
    symbol: str,
    *,
    name: str | None = None,
    weight: float | None,
    order: int,
    source: CompanySourceIdentity = SSGA,
) -> NormalizedCompanyConstituent:
    return NormalizedCompanyConstituent.from_source(
        source=source,
        symbol=symbol,
        name=name or f"{symbol} Corp",
        weight=weight,
        order=order,
    )


def test_weight_rank_and_tier_are_derived_from_descending_weight() -> None:
    companies = [
        constituent("AAPL", weight=6.4, order=2, name="Apple Inc."),
        constituent("NVDA", weight=7.9, order=1, name="NVIDIA CORP"),
        constituent("ACME", weight=0.7, order=3, name="Acme Corp"),
    ]

    enriched = enrich_sp500_rank_and_tier(companies)

    assert [
        (
            company.symbol,
            company.sp500_weight_rank,
            company.sp500_tier,
            company.sp500_rank_source,
            company.sp500_rank_status,
        )
        for company in enriched
    ] == [
        ("NVDA", 1, "mag7", "ssga_spy_holdings:weight", WEIGHT_DERIVED_RANK_STATUS),
        ("AAPL", 2, "mag7", "ssga_spy_holdings:weight", WEIGHT_DERIVED_RANK_STATUS),
        ("ACME", 3, "top100", "ssga_spy_holdings:weight", WEIGHT_DERIVED_RANK_STATUS),
    ]
    payload = enriched[0].to_company_payload()
    assert payload["sp500_weight_rank"] == 1
    assert payload["sp500_tier"] == "mag7"
    assert payload["sp500_rank_source"] == "ssga_spy_holdings:weight"
    assert payload["sp500_rank_status"] == WEIGHT_DERIVED_RANK_STATUS


def test_mag7_includes_both_alphabet_share_classes_and_overrides_top_bucket() -> None:
    assert (
        assign_sp500_tier(symbol="GOOG", name="Alphabet Inc. Class C", weight_rank=99)
        == "mag7"
    )
    assert (
        assign_sp500_tier(symbol="GOOGL", name="Alphabet Inc. Class A", weight_rank=100)
        == "mag7"
    )
    assert (
        assign_sp500_tier(symbol="AAPL", name="Apple Inc.", weight_rank=101) == "mag7"
    )


def test_top_tier_boundaries_use_weight_rank_for_non_mag7_companies() -> None:
    assert (
        assign_sp500_tier(symbol="R100", name="Rank 100 Corp", weight_rank=100)
        == "top100"
    )
    assert (
        assign_sp500_tier(symbol="R101", name="Rank 101 Corp", weight_rank=101)
        == "top200"
    )
    assert (
        assign_sp500_tier(symbol="R200", name="Rank 200 Corp", weight_rank=200)
        == "top200"
    )
    assert (
        assign_sp500_tier(symbol="R201", name="Rank 201 Corp", weight_rank=201)
        == "top300"
    )
    assert (
        assign_sp500_tier(symbol="R300", name="Rank 300 Corp", weight_rank=300)
        == "top300"
    )
    assert (
        assign_sp500_tier(symbol="R301", name="Rank 301 Corp", weight_rank=301)
        == "top400"
    )
    assert (
        assign_sp500_tier(symbol="R400", name="Rank 400 Corp", weight_rank=400)
        == "top400"
    )
    assert (
        assign_sp500_tier(symbol="R401", name="Rank 401 Corp", weight_rank=401)
        == "top500"
    )
    assert (
        assign_sp500_tier(symbol="R500", name="Rank 500 Corp", weight_rank=500)
        == "top500"
    )
    assert (
        assign_sp500_tier(symbol="R501", name="Rank 501 Corp", weight_rank=501) is None
    )


def test_equal_weights_use_provider_order_then_ticker_as_tie_breakers() -> None:
    companies = [
        constituent("ZZZ", weight=1.0, order=2),
        constituent("AAA", weight=1.0, order=2),
        constituent("MMM", weight=1.0, order=1),
    ]

    enriched = enrich_sp500_rank_and_tier(companies)

    assert [(company.symbol, company.sp500_weight_rank) for company in enriched] == [
        ("MMM", 1),
        ("AAA", 2),
        ("ZZZ", 3),
    ]


def test_missing_weight_does_not_fabricate_rank_or_top_bucket_by_default() -> None:
    companies = [
        constituent("AAPL", weight=None, order=1, name="Apple Inc.", source=WIKIPEDIA),
        constituent("ACME", weight=None, order=2, source=WIKIPEDIA),
    ]

    enriched = enrich_sp500_rank_and_tier(companies)

    assert [
        (
            company.symbol,
            company.sp500_weight_rank,
            company.sp500_tier,
            company.sp500_rank_source,
            company.sp500_rank_status,
        )
        for company in enriched
    ] == [
        ("AAPL", None, "mag7", None, UNAVAILABLE_RANK_STATUS),
        ("ACME", None, None, None, UNAVAILABLE_RANK_STATUS),
    ]


def test_fallback_source_order_rank_is_opt_in_and_marked() -> None:
    companies = [
        constituent("BETA", weight=None, order=2, source=WIKIPEDIA),
        constituent("ALFA", weight=None, order=1, source=WIKIPEDIA),
    ]

    enriched = enrich_sp500_rank_and_tier(companies, allow_fallback_rank=True)

    assert [
        (
            company.symbol,
            company.sp500_weight_rank,
            company.sp500_tier,
            company.sp500_rank_source,
            company.sp500_rank_status,
        )
        for company in enriched
    ] == [
        (
            "ALFA",
            1,
            "top100",
            "wikipedia_sp500_enrichment:source_order",
            FALLBACK_SOURCE_ORDER_RANK_STATUS,
        ),
        (
            "BETA",
            2,
            "top100",
            "wikipedia_sp500_enrichment:source_order",
            FALLBACK_SOURCE_ORDER_RANK_STATUS,
        ),
    ]
