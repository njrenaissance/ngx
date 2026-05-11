from collections.abc import Callable

from fastapi import APIRouter

from forge import __version__, db
from forge.config import settings

router = APIRouter(tags=["health"])

# Service name → readiness check function. Each function returns
# (healthy: bool, detail: str). Add new dependencies here as one line:
#   "redis": redis.readiness_check,
READINESS_CHECKS: dict[str, Callable[[], tuple[bool, str]]] = {
    "db": db.readiness_check,
}


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
def readyz() -> dict[str, object]:
    """Readiness probe — runs all registered checks and reports each result.

    Always returns HTTP 200 (soft readiness). The top-level `status` is
    `"ok"` when every check passes and `"degraded"` when any check fails.
    Per-service detail lives under `checks`. We intentionally do NOT return
    503 on failure yet — with a single ECS task and no replicas to fail over
    to, that would just take the whole service offline. Flip to strict when
    we have more than one task in the target group.
    """
    checks: dict[str, dict[str, str]] = {}
    all_ok = True

    for name, check_fn in READINESS_CHECKS.items():
        ok, detail = check_fn()
        checks[name] = {"status": "ok" if ok else "error", "detail": detail}
        if not ok:
            all_ok = False

    return {
        "status": "ok" if all_ok else "degraded",
        "checks": checks,
    }
