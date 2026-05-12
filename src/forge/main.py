from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from forge import __version__
from forge.api.health import router as health_router
from forge.api.me import router as me_router
from forge.config import settings


def get_app() -> FastAPI:
    """Application factory.

    Returns a configured FastAPI instance. Using a factory delays heavyweight
    initialization until invocation and lets tests build fresh app instances.
    """
    application = FastAPI(
        title=f"{settings.APP_NAME} — Infrastructure Provisioning Service",
        version=__version__,
    )

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    application.include_router(health_router)
    application.include_router(me_router)
    return application


# Module-level instance for uvicorn: `uvicorn forge.main:app`
app = get_app()
