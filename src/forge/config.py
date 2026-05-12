from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource


class DatabaseSettings(BaseSettings):
    """Database connection settings.

    Reads from environment variables with the FORGE_DATABASE__ prefix, e.g.:
      FORGE_DATABASE__HOST, FORGE_DATABASE__PORT, FORGE_DATABASE__PASSWORD

    Shares the FORGE_ namespace with Settings so all app config lives under
    one prefix. Nested in Settings via Field(default_factory=...).
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_DATABASE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    HOST: str = "localhost"
    PORT: int = 5432
    NAME: str = "forge"
    USER: str = "forge"
    PASSWORD: str = ""
    SSL_MODE: str = "disable"
    SCHEMA: str = "public"

    # Connection pool — tune for Aurora Serverless v2 idle-connection behaviour.
    CONNECT_TIMEOUT: int = 10  # seconds to wait for TCP connect to the DB host
    POOL_TIMEOUT: int = 30  # seconds a caller waits when the pool is exhausted
    POOL_RECYCLE: int = 1800  # recycle connections every 30 min (< Aurora idle timeout)

    @property
    def url(self) -> str:
        """asyncpg DSN — used by the async SQLAlchemy engine at runtime."""
        base = f"postgresql+asyncpg://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.NAME}"
        if self.SCHEMA != "public":
            base += f"?server_settings[search_path]={self.SCHEMA}"
        return base

    @property
    def sync_url(self) -> str:
        """psycopg2 DSN — used by Alembic (sync) and the seed script."""
        base = f"postgresql+psycopg2://{self.USER}:{self.PASSWORD}@{self.HOST}:{self.PORT}/{self.NAME}"
        params = [f"sslmode={self.SSL_MODE}"]
        if self.SCHEMA != "public":
            params.append(f"options=-csearch_path%3D{self.SCHEMA}")
        return base + "?" + "&".join(params)


# Baseline configuration — lowest-priority layer. Environment variables
# (prefixed FORGE_) and an optional .env file override these.
DEFAULT_SETTINGS: dict[str, Any] = {
    "APP_NAME": "Forge",
    "ENVIRONMENT": "dev",
    "LOG_LEVEL": "INFO",
    "HOST": "0.0.0.0",
    "PORT": 8000,
    "RELOAD": False,
    "REQUEST_TIMEOUT": 30,
    "KEEPALIVE_TIMEOUT": 2,
    "SHUTDOWN_TIMEOUT": 25,
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

    # Request handling — tune these together with the ALB and ECS stopTimeout.
    # REQUEST_TIMEOUT < ALB idle timeout (60 s default) — cancel slow handlers
    #   before the ALB returns a 504 to the client.
    # KEEPALIVE_TIMEOUT < ALB idle timeout — prevent the ALB from closing a
    #   connection that uvicorn still considers alive.
    # SHUTDOWN_TIMEOUT < ECS stopTimeout (30 s default) — drain in-flight
    #   requests before ECS sends SIGKILL.
    REQUEST_TIMEOUT: int
    KEEPALIVE_TIMEOUT: int
    SHUTDOWN_TIMEOUT: int

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)

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
