"""Shared SQLite connection helpers.

FastAPI handles web requests; this file handles the database boundary.
Keeping those concerns separate makes route functions easier to read.
"""

from collections.abc import Iterator
import sqlite3

from app.config import get_database_path


def connect() -> sqlite3.Connection:
    """Open a connection to the hunter-agent SQLite database.

    SQLite connections are lightweight enough for this beginner app. We open
    one when a route needs database work and close it when the work is done.
    """
    connection = sqlite3.connect(get_database_path())
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def get_connection() -> Iterator[sqlite3.Connection]:
    """FastAPI dependency that gives a route a database connection.

    The `yield` pattern lets FastAPI run cleanup code after the response.
    TODO: Use this dependency in write routes when you add create/update logic.
    """
    connection = connect()
    try:
        yield connection
    finally:
        connection.close()
