"""Health check routes.

Health checks are tiny endpoints used to confirm the app process is alive.
They are useful before the rest of the product is finished.
"""

from fastapi import APIRouter


router = APIRouter()


@router.get("/health")
def health_check() -> dict[str, str]:
    """Return a simple JSON response for uptime checks."""
    return {"status": "ok"}
