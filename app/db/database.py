"""Shared SQLModel database helpers.

FastAPI handles web requests; this file handles the database boundary.
Keeping those concerns separate makes route functions easier to read.
"""

from collections.abc import Iterator

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, create_engine

from app.config import get_database_path


DATABASE_URL = f"sqlite:///{get_database_path()}"
engine = create_engine(DATABASE_URL, echo=False)


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    """Ask SQLite to enforce foreign keys for every new connection.

    SQLite supports foreign keys, but they are disabled by default per
    connection. SQLModel uses SQLAlchemy underneath, so we hook into SQLAlchemy's
    connection event to enable the pragma once.
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency that gives a route a SQLModel session.

    A session is the unit of work for ORM reads and writes. Keep it request
    scoped so each request has a clean database boundary.
    """
    with Session(engine) as session:
        yield session
