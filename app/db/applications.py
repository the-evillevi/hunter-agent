"""Early application-query helpers.

This file preserves the first database experiment, but makes it safer:
the caller provides the connection, and the shared database module decides
where the SQLite file lives.
"""

from sqlite3 import Connection, Row


def get_applications(connection: Connection) -> list[Row]:
    """Return recent application rows.

    TODO: Join this with the jobs table when you build an applications page.
    """
    cursor = connection.execute(
        """
        SELECT *
        FROM applications
        ORDER BY last_updated DESC
        LIMIT 100
        """
    )
    return cursor.fetchall()
