"""Placeholder scraping service.

The scraper will eventually read enabled sources from `config.toml`, fetch job
postings, normalize them, and pass them to `services.jobs` for storage.
"""


def scrape_jobs() -> list[dict]:
    """Return scraped jobs from configured sources.

    This is deliberately not implemented yet. Build this slowly:
    1. Start with one public source like Remotive.
    2. Convert the API response into your own job dictionary shape.
    3. Save only the fields that already exist in `sql/hunter-agent.sql`.
    """
    # TODO: Implement the first real scraper after the web skeleton is clear.
    return []
