"""Entry point invoked by `python -m forge`.

Wraps uvicorn so that host/port/reload come from the unified Forge settings
layer (FORGE_HOST, FORGE_PORT, FORGE_RELOAD) instead of being hardcoded in
the Dockerfile or compose command.
"""

import uvicorn

from forge.config import settings


def main() -> None:
    uvicorn.run(
        "forge.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.RELOAD,
        log_level=settings.log.LEVEL.lower(),
    )


if __name__ == "__main__":
    main()
