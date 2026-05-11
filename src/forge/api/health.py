from fastapi import APIRouter

from forge import __version__
from forge.config import settings

router = APIRouter(tags=["health"])


@router.get("/livez")
def livez() -> dict[str, str]:
    """Liveness probe — the process is running and able to handle requests.

    Consumed by the orchestrator (ECS task health, K8s liveness probe) to decide
    whether the container should be restarted. Includes service identity for
    quick "is this thing reachable and what version?" checks.
    """
    return {
        "status": "ok",
        "message": f"{settings.APP_NAME} version {__version__} is running",
        "version": __version__,
    }


@router.get("/readyz")
def readyz() -> dict[str, str]:
    """Readiness probe — the process is ready to serve traffic.

    Consumed by the load balancer (ALB target group) to decide whether to route
    requests to this instance. Currently a stub; later PRs will check downstream
    dependencies (DB, Redis).
    """
    return {"status": "ok"}
