from sqlmodel import Field, SQLModel


class Source(SQLModel, table=True):
    """Job source table from sql/hunter-agent.sql."""

    __tablename__ = "sources"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
