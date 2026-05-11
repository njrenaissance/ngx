from functools import lru_cache
from typing import Any

from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

# Baseline configuration — lowest-priority layer. Environment variables
# (prefixed FORGE_) and an optional .env file override these.
DEFAULT_SETTINGS: dict[str, Any] = {
    "APP_NAME": "Forge",
    "ENVIRONMENT": "dev",
    "LOG_LEVEL": "INFO",
    "HOST": "0.0.0.0",
    "PORT": 8000,
    "RELOAD": False,
}


class Settings(BaseSettings):
    """Application configuration.

    Resolution order (highest wins):
      1. Init kwargs passed to Settings(...)
      2. Environment variables (FORGE_APP_NAME, FORGE_LOG_LEVEL, ...)
      3. .env file (FORGE_ prefix also applies)
      4. DEFAULT_SETTINGS dict (lowest priority)
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str
    ENVIRONMENT: str
    LOG_LEVEL: str
    HOST: str
    PORT: int
    RELOAD: bool

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # First tuple element has highest priority; DEFAULT_SETTINGS goes last.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            InitSettingsSource(settings_cls, init_kwargs=DEFAULT_SETTINGS),
        )


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that DEFAULT_SETTINGS supplies values via the custom
    # InitSettingsSource — values are resolved at runtime, not call time.
    return Settings()  # type: ignore[call-arg]


# Module-level singleton — import as `from forge.config import settings`.
settings = get_settings()
