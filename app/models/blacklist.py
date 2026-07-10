"""Blacklist table model.

Mirrors the blacklist table in sql/hunter-agent.sql: one row blocks either
a single job or a whole company (exactly one target, enforced by a CHECK
constraint), with a free-text reason. Removal deletes the row — this is a
personal blacklist, not an audit trail (decided on HNTR-52).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlmodel import Field, SQLModel


class Blacklist(SQLModel, table=True):
    """One blocked job or company with the user's reason."""

    __tablename__ = "blacklist"
    __table_args__ = (
        CheckConstraint(
            "(company_id IS NOT NULL AND job_id IS NULL)"
            " OR (company_id IS NULL AND job_id IS NOT NULL)",
            name="ck_blacklist_exactly_one_target",
        ),
        UniqueConstraint("company_id", name="uq_blacklist_company_id"),
        UniqueConstraint("job_id", name="uq_blacklist_job_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    company_id: int | None = Field(default=None, foreign_key="companies.id")
    job_id: int | None = Field(default=None, foreign_key="jobs.id")
    reason: str | None = None
    # The SQL column is NOT NULL without a default, so this default_factory
    # is what makes model-driven inserts valid.
    added_at: datetime = Field(default_factory=datetime.now)
