from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import RedirectResponse

from forge import __version__
from forge.api.catalog import router as catalog_router
from forge.api.health import router as health_router
from forge.api.me import router as me_router
from forge.api.resources import router as resources_router
from forge.config import settings


def _custom_openapi(application: FastAPI):  # type: ignore[return]
    def openapi() -> dict:
        if application.openapi_schema:
            return application.openapi_schema
        schema = get_openapi(
            title=application.title,
            version=application.version,
            routes=application.routes,
        )
        schema.setdefault("components", {})["securitySchemes"] = {"BearerAuth": {"type": "http", "scheme": "bearer"}}
        schema["security"] = [{"BearerAuth": []}]
        application.openapi_schema = schema
        return schema

    return openapi


def get_app() -> FastAPI:
    application = FastAPI(
        title=f"{settings.APP_NAME} — Infrastructure Provisioning Service",
        version=__version__,
    )

    @application.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/docs")

    application.include_router(health_router)
    application.include_router(me_router)
    application.include_router(catalog_router)
    application.include_router(resources_router)

    application.openapi = _custom_openapi(application)  # type: ignore[method-assign]
    return application


# Module-level instance for uvicorn: `uvicorn forge.main:app`
app = get_app()
