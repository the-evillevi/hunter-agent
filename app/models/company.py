"""Company database and display models."""

from sqlmodel import Field, SQLModel


class Company(SQLModel, table=True):
    """Company table from sql/hunter-agent.sql."""

    __tablename__ = "companies"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
