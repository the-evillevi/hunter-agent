from sqlmodel import Field, SQLModel


class Location(SQLModel, table=True):
    """Location table from sql/hunter-agent.sql."""

    __tablename__ = "locations"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(unique=True)
