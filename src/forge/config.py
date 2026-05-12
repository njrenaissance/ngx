from functools import lru_cache
from typing import Any

from pydantic import Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

# Baseline configuration — lowest-priority layer. Environment variables
# (prefixed FORGE_) and an optional .env file override these per-field.
#
# Nested settings classes (database, celery) draw their defaults from the
# matching nested dict here via the _build_customise_sources(...) helper
# below. PASSWORD and the Celery broker URL default to empty strings so
# the process boots; connect-time failures surface missing config.
#
# When adding a new nested BaseSettings class, also add its top-level key
# to _NESTED_SETTINGS_KEYS so Settings.settings_customise_sources knows
# to exclude its dict from the top-level baseline.
DEFAULT_SETTINGS: dict[str, Any] = {
    "APP_NAME": "Forge",
    "ENVIRONMENT": "dev",
    "HOST": "0.0.0.0",
    "PORT": 8000,
    "RELOAD": False,
    "REQUEST_TIMEOUT": 30,
    "KEEPALIVE_TIMEOUT": 2,
    "SHUTDOWN_TIMEOUT": 25,
    "database": {
        "HOST": "localhost",
        "PORT": 5432,
        "NAME": "forge",
        "USER": "forge",
        "PASSWORD": "",
        "SSL_MODE": "disable",
        "SCHEMA": "public",
        "CONNECT_TIMEOUT": 10,
        "POOL_TIMEOUT": 30,
        "POOL_RECYCLE": 1800,
    },
    "celery": {
        "BROKER_URL": "",
        # Cross-file invariant: this queue name must match the `-Q` flag on the
        # worker's celery command in infrastructure/modules/ecs_service/main.tf
        # (search for "-Q", "provisioning"). The worker only consumes the
        # queue(s) it's started with; a mismatch means tasks accumulate in
        # the broker forever with no consumer. There is no enforced single
        # source of truth — change requires editing both places.
        "TASK_DEFAULT_QUEUE": "provisioning",
        "TASK_TIME_LIMIT": 1800,  # 30 min hard kill — covers a slow terraform apply
        "TASK_SOFT_TIME_LIMIT": 1500,  # 25 min soft — leaves room for in-task cleanup
    },
    "terraform": {
        # Empty strings fail loud at materialize-time when the worker tries to
        # render backend.tf — cloud envs must set FORGE_TERRAFORM__MANAGED_RESOURCES_*.
        "MANAGED_RESOURCES_BUCKET": "",
        "MANAGED_RESOURCES_REGION": "",
        "PACKAGES_DIR": "./packages",
    },
    "log": {
        # DEBUG default surfaces startup, db init, task lifecycle, and AZ
        # selection events in production logs. Bump to INFO/WARNING via
        # FORGE_LOG__LEVEL once the system is stable and the noise becomes
        # a cost concern.
        "LEVEL": "DEBUG",
        "JSON_INDENT": None,
    },
}

# Top-level keys whose values are nested settings dicts (not Settings fields).
# Each entry corresponds to a BaseSettings subclass with its own env-var prefix
# and its own _build_customise_sources binding. Anything NOT in this set is
# treated as a flat Settings field and flows into the top-level baseline.
# Failing to add a new nested class here means its defaults dict would leak
# into Settings as a stray field at construction time.
_NESTED_SETTINGS_KEYS: frozenset[str] = frozenset({"database", "celery", "terraform", "log"})


def _build_customise_sources(defaults_key: str):
    """Return a settings_customise_sources classmethod that pulls baselines
    from DEFAULT_SETTINGS[defaults_key] at the lowest priority.

    Resolution order (highest wins): init_kwargs > env > .env > DEFAULT_SETTINGS.
    """

    @classmethod  # type: ignore[misc]
    def _customise(
        _cls: type[BaseSettings],
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            InitSettingsSource(settings_cls, init_kwargs=DEFAULT_SETTINGS[defaults_key]),
        )

    return _customise


class LogSettings(BaseSettings):
    """Structured JSON logging settings.

    Baseline values live in DEFAULT_SETTINGS["log"]. Override at runtime via
    FORGE_LOG__LEVEL and FORGE_LOG__JSON_INDENT environment variables.

    Direct construction note: always import via `from forge.config import settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_LOG__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    LEVEL: str
    # Pretty-prints JSON across multiple lines when set (useful for local
    # eyeballing). MUST stay None in production: log aggregators (CloudWatch,
    # Datadog, etc.) parse one JSON record per line and an indented record
    # spans many lines, breaking ingestion.
    JSON_INDENT: int | None = None

    settings_customise_sources = _build_customise_sources("log")


class CelerySettings(BaseSettings):
    """Celery / Redis broker settings.

    Local dev points BROKER_URL at the docker-compose redis service.
    Production points it at an AWS Elasticache for Redis primary endpoint
    via the ECS task definition. Use rediss:// for TLS — the app has no
    transport-mode branches.

    The result backend is derived from DatabaseSettings.sync_url at app
    startup (see forge.workers.__init__) so we reuse the same Aurora
    cluster for task results.

    Baseline values live in DEFAULT_SETTINGS["celery"]. Env vars
    (FORGE_CELERY__BROKER_URL etc.) override them at runtime.

    Direct construction note: `CelerySettings()` is wired to read its
    baseline from DEFAULT_SETTINGS via _build_customise_sources. Bypassing
    that path (e.g. instantiating a bare BaseSettings clone in a one-off
    script) will raise ValidationError for missing fields. Always import
    via `from forge.config import settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_CELERY__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    BROKER_URL: str
    TASK_DEFAULT_QUEUE: str
    # Hard time limit in seconds — Celery SIGKILLs the worker child after
    # this. Long enough for `terraform apply` on a database to finish,
    # short enough to recover a stuck worker. Tune per environment via
    # FORGE_CELERY__TASK_TIME_LIMIT.
    TASK_TIME_LIMIT: int
    # Soft time limit raises SoftTimeLimitExceeded inside the task so it
    # can clean up before the hard kill. Should be lower than TASK_TIME_LIMIT.
    TASK_SOFT_TIME_LIMIT: int

    settings_customise_sources = _build_customise_sources("celery")


class TerraformSettings(BaseSettings):
    """Terraform workspace / managed-resources settings.

    The worker materializes per-request Terraform workspaces under
    /tmp/forge-workspaces/ by copying the matching versioned package from
    PACKAGES_DIR and rendering a backend.tf pointing at the S3 state bucket.

    MANAGED_RESOURCES_BUCKET / MANAGED_RESOURCES_REGION are blank in
    DEFAULT_SETTINGS so cloud envs must set
    FORGE_TERRAFORM__MANAGED_RESOURCES_BUCKET and
    FORGE_TERRAFORM__MANAGED_RESOURCES_REGION explicitly. PACKAGES_DIR
    defaults to ./packages (resolved relative to the worker's CWD inside the
    container — docker-compose mounts the host packages/ tree there).

    Direct construction note: `TerraformSettings()` is wired to read its
    baseline from DEFAULT_SETTINGS via _build_customise_sources. Bypassing
    that path will raise ValidationError for missing fields. Always import
    via `from forge.config import settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_TERRAFORM__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    MANAGED_RESOURCES_BUCKET: str
    MANAGED_RESOURCES_REGION: str
    PACKAGES_DIR: str

    settings_customise_sources = _build_customise_sources("terraform")


class DatabaseSettings(BaseSettings):
    """Database connection settings.

    Reads from environment variables with the FORGE_DATABASE__ prefix, e.g.:
      FORGE_DATABASE__HOST, FORGE_DATABASE__PORT, FORGE_DATABASE__PASSWORD

    Baseline values live in DEFAULT_SETTINGS["database"]. Env vars override
    them at runtime.

    Direct construction note: `DatabaseSettings()` is wired to read its
    baseline from DEFAULT_SETTINGS via _build_customise_sources. Bypassing
    that path (e.g. instantiating a bare BaseSettings clone in a one-off
    script) will raise ValidationError for missing fields. Always import
    via `from forge.config import settings`.
    """

    model_config = SettingsConfigDict(
        env_prefix="FORGE_DATABASE__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    HOST: str
    PORT: int
    NAME: str
    USER: str
    PASSWORD: str
    SSL_MODE: str
    SCHEMA: str

    # Connection pool — tune for Aurora Serverless v2 idle-connection behaviour.
    CONNECT_TIMEOUT: int  # seconds to wait for TCP connect to the DB host
    POOL_TIMEOUT: int  # seconds a caller waits when the pool is exhausted
    POOL_RECYCLE: int  # recycle interval (< Aurora idle timeout)

    settings_customise_sources = _build_customise_sources("database")

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


class Settings(BaseSettings):
    """Application configuration.

    Resolution order (highest wins):
      1. Init kwargs passed to Settings(...)
      2. Environment variables (FORGE_APP_NAME, FORGE_LOG__LEVEL, ...)
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

    # Nested classes own their own env-var loading and DEFAULT_SETTINGS lookup
    # via _build_customise_sources. They construct themselves from () here.
    database: DatabaseSettings = Field(default_factory=lambda: DatabaseSettings())  # type: ignore[call-arg]
    celery: CelerySettings = Field(default_factory=lambda: CelerySettings())  # type: ignore[call-arg]
    terraform: TerraformSettings = Field(default_factory=lambda: TerraformSettings())  # type: ignore[call-arg]
    log: LogSettings = Field(default_factory=lambda: LogSettings())  # type: ignore[call-arg]

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
        # Only top-level keys flow in here — nested classes handle their own
        # via _build_customise_sources. We explicitly exclude
        # _NESTED_SETTINGS_KEYS rather than filtering on isinstance(v, dict)
        # so a future non-dict baseline structure can't silently slip through.
        top_level_defaults = {k: v for k, v in DEFAULT_SETTINGS.items() if k not in _NESTED_SETTINGS_KEYS}
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            file_secret_settings,
            InitSettingsSource(settings_cls, init_kwargs=top_level_defaults),
        )


@lru_cache
def get_settings() -> Settings:
    # mypy can't see that DEFAULT_SETTINGS supplies values via the custom
    # InitSettingsSource — values are resolved at runtime, not call time.
    return Settings()  # type: ignore[call-arg]


# Module-level singleton — import as `from forge.config import settings`.
settings = get_settings()
