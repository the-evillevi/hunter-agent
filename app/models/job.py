"""Job data shapes.

The database is still the source of truth. This dataclass is here so beginners
can see what fields a "job" has without jumping into SQL first.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Job:
    """A simple view of a job listing shown in the web UI.

    TODO: Add fields as your scraper starts collecting salary, tags, and source
    metadata that you want to display.
    """

    id: int
    title: str
    company: str
    location: str
    source: str
    score: int | None
    url: str | None
