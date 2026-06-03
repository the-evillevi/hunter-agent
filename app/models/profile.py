from sqlmodel import Field, SQLModel


class Profile(SQLModel, table=True):
    """Minimal profile table model needed by Job's foreign key.

    TODO: Expand this when you build profile management screens.
    """

    __tablename__ = "profiles"

    id: int | None = Field(default=None, primary_key=True)
    role_name: str | None = None
    salary_min: int | None = None
    location_type: str | None = None
    match_threshold: int | None = None
    active: bool
