"""Service and route tests for the paginated companies page."""

from datetime import date

import pytest
from sqlmodel import Session

from app.models.company import Company
from app.services.companies import list_companies


def add_company(
    session: Session,
    *,
    name: str,
    ticker: str,
    rank: int | None,
    current: bool = True,
    tier: str | None = None,
) -> Company:
    company = Company(
        name=name,
        ticker=ticker,
        sector="Information Technology",
        is_sp500=current,
        sp500_weight_rank=rank,
        sp500_tier=tier,
        sp500_weight=1.234,
        sp500_holdings_as_of=date(2026, 6, 19),
    )
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


def test_list_companies_defaults_to_current_ranked_order(session: Session) -> None:
    add_company(session, name="Unranked Co", ticker="UNR", rank=None)
    add_company(session, name="Zulu Co", ticker="ZZZ", rank=2)
    add_company(session, name="Alpha Co", ticker="AAA", rank=2)
    add_company(
        session,
        name="Historical Co",
        ticker="OLD",
        rank=1,
        current=False,
    )

    result = list_companies(session)

    assert [company.ticker for company in result.companies] == ["AAA", "ZZZ", "UNR"]
    assert result.total == 3
    assert result.total_pages == 1
    assert result.previous_page is None
    assert result.next_page is None


def test_list_companies_search_and_filters_compose(session: Session) -> None:
    add_company(
        session,
        name="Alphabet Class A",
        ticker="GOOGL",
        rank=5,
        tier="top100",
    )
    add_company(
        session,
        name="Alphabet Alumni",
        ticker="OLDG",
        rank=None,
        current=False,
        tier="top100",
    )
    add_company(
        session,
        name="Alphabet Class C",
        ticker="GOOG",
        rank=6,
        tier="top200",
    )

    result = list_companies(
        session,
        q="oldg",
        membership="all",
        tier="top100",
    )

    assert [company.name for company in result.companies] == ["Alphabet Alumni"]


def test_list_companies_returns_bounded_pagination_metadata(session: Session) -> None:
    for index in range(5):
        add_company(
            session,
            name=f"Company {index}",
            ticker=f"C{index}",
            rank=index + 1,
        )

    second_page = list_companies(session, page=2, page_size=2)
    out_of_range = list_companies(session, page=9, page_size=2)

    assert [company.ticker for company in second_page.companies] == ["C2", "C3"]
    assert second_page.total == 5
    assert second_page.total_pages == 3
    assert second_page.previous_page == 1
    assert second_page.next_page == 3
    assert out_of_range.companies == []
    assert out_of_range.is_out_of_range is True
    assert out_of_range.previous_page == 3


def test_companies_page_renders_navigation_filters_and_first_page(
    client,
    session: Session,
) -> None:
    add_company(
        session,
        name="Apple Inc.",
        ticker="AAPL",
        rank=1,
        tier="mag7",
    )

    response = client.get("/companies")

    assert response.status_code == 200
    assert "<!doctype html>" in response.text
    assert 'href="/companies"' in response.text
    assert 'hx-get="/companies/partials/table"' in response.text
    assert "Apple Inc." in response.text
    assert "AAPL" in response.text
    assert "Information Technology" in response.text
    assert "1.234%" in response.text
    assert "2026-06-19" in response.text


def test_companies_fragment_returns_only_table_and_preserves_filters(
    client,
    session: Session,
) -> None:
    for index in range(3):
        add_company(
            session,
            name=f"Acme {index}",
            ticker=f"AC{index}",
            rank=index + 1,
            tier="top100",
        )

    response = client.get(
        "/companies/partials/table",
        params={
            "q": "Acme",
            "membership": "current",
            "tier": "top100",
            "page_size": 2,
        },
    )

    assert response.status_code == 200
    assert "<!doctype html>" not in response.text
    assert "Page 1 of 2" in response.text
    assert "q=Acme" in response.text
    assert "membership=current" in response.text
    assert "tier=top100" in response.text
    assert "page_size=2" in response.text


def test_companies_page_renders_empty_and_out_of_range_states(
    client,
    session: Session,
) -> None:
    empty_response = client.get("/companies")
    add_company(session, name="Only Company", ticker="ONLY", rank=1)
    out_of_range_response = client.get("/companies", params={"page": 2})

    assert "No companies found" in empty_response.text
    assert "That page is out of range" in out_of_range_response.text
    assert "Go to last page" in out_of_range_response.text


@pytest.mark.parametrize(
    "parameters",
    [
        {"page": 0},
        {"page_size": 0},
        {"page_size": 101},
        {"membership": "former"},
        {"tier": "top50"},
    ],
)
def test_companies_routes_reject_invalid_query_values(client, parameters) -> None:
    response = client.get("/companies", params=parameters)

    assert response.status_code == 422


def test_company_ui_routes_are_excluded_from_openapi(client) -> None:
    # The project-wide visibility policy lives in tests/test_openapi.py; this
    # covers the company surfaces specifically.
    paths = client.get("/openapi.json").json()["paths"]

    assert "/api/companies/sp500/ingest" in paths
    assert "/companies" not in paths
    assert "/companies/partials/table" not in paths
    assert "/companies/sp500/ingest" not in paths
