"""Health check routes.

Health checks are tiny endpoints used to confirm the app process is alive.
They are useful before the rest of the product is finished.
"""

from fastapi import APIRouter
from sqlmodel import SQLModel


router = APIRouter(tags=["Monitoring"])


class HealthStatus(SQLModel):
    """Response shape for the health check, so /docs shows a real schema."""

    status: str


@router.get(
    "/health",
    summary="Check that the app process is alive",
    response_model=HealthStatus,
)
def health_check() -> HealthStatus:
    """Return a simple JSON response for uptime checks."""
    return HealthStatus(status="ok")
