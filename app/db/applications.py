"""Application-query helpers.

This file is not used by the current UI yet. It is kept as a placeholder for
the future applications page, using SQLModel sessions instead of raw sqlite3.
"""

from sqlalchemy import text
from sqlmodel import Session


def get_applications(session: Session) -> list[dict]:
    """Return recent application rows.

    TODO: Replace this text query with SQLModel table models when you build the
    applications page.
    """
    rows = session.exec(
        text(
            """
        SELECT *
        FROM applications
        ORDER BY last_updated DESC
        LIMIT 100
        """
        )
    ).mappings()
    return [dict(row) for row in rows]
