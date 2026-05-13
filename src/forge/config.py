import os
import warnings
from functools import lru_cache
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from pydantic_settings.sources import InitSettingsSource

# DatabaseSettings declares a `schema` field — that name shadows the
# deprecated BaseModel.schema() method, so pydantic emits a UserWarning
# at class-creation time. The shadow is intentional (we want the lowercase
# `schema` attribute to match the FORGE_DATABASE__SCHEMA env var contract);
# silence the warning module-wide so app startup logs stay clean.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" in "DatabaseSettings" shadows an attribute in parent',
    category=UserWarning,
)

# Baseline configuration — lowest-priority layer. Environment variables
# (prefixed FORGE_) and an optional .env file override these per-field.
#
# Keys mirror the lowercase Python attribute names on the BaseSettings
# subclasses below. Env vars stay uppercase (FORGE_HOST, FORGE_LOG__LEVEL)
# — pydantic-settings is case-insensitive by default, so FORGE_HOST maps
# to the `host` field automatically.
#
# Nested settings classes (database, celery, terraform, log) draw their
# defaults from the matching nested dict here via the
# _build_customise_sources(...) helper below. PASSWORD and the Celery
# broker URL default to empty strings so the process boots; connect-time
# failures surface missing config.
#
# When adding a new nested BaseSettings class, also add its top-level key
# to _NESTED_SETTINGS_KEYS so Settings.settings_customise_sources knows
# to exclude its dict from the top-level baseline.
DEFAULT_SETTINGS: dict[str, Any] = {
    "app_name": "Forge",
    "environment": "dev",
    # Loopback by default — safer for `python -m forge` on a dev laptop.
    # docker-compose and the ECS task definition override to 0.0.0.0
    # via FORGE_HOST so production behaviour is unchanged.
    "host": "127.0.0.1",
    "port": 8000,
    "reload": False,
    "request_timeout": 30,
    "keepalive_timeout": 2,
    "shutdown_timeout": 25,
    "database": {
        "host": "localhost",
        "port": 5432,
        "name": "forge",
        "user": "forge",
        "password": "",
        "ssl_mode": "disable",
        "schema": "public",
        "connect_timeout": 10,
        "pool_timeout": 30,
        "pool_recycle": 1800,
    },
    "celery": {
        "broker_url": "",
        # Cross-file invariant: this queue name must match the `-Q` flag on the
        # worker's celery command in infrastructure/modules/ecs_service/main.tf
        # (search for "-Q", "provisioning"). The worker only consumes the
        # queue(s) it's started with; a mismatch means tasks accumulate in
        # the broker forever with no consumer. There is no enforced single
        # source of truth — change requires editing both places.
        "task_default_queue": "provisioning",
        "task_time_limit": 1800,  # 30 min hard kill — covers a slow terraform apply
        "task_soft_time_limit": 1500,  # 25 min soft — leaves room for in-task cleanup
    },
    "terraform": {
        # Empty strings fail loud at materialize-time when the worker tries to
        # render backend.tf — cloud envs must set FORGE_TERRAFORM__MANAGED_RESOURCES_*.
        "managed_resources_bucket": "",
        "managed_resources_region": "",
        "packages_dir": "./packages",
        # Defaults to the `terraform` binary on PATH. Tests override via
        # FORGE_TERRAFORM__BINARY to point at a deterministic fake script —
        # accepts a shell-style command (e.g. "python /path/to/fake.py") which
        # TerraformRunner splits with shlex before invoking subprocess.
        "binary": "terraform",
    },
    "log": {
        # DEBUG default surfaces startup, db init, task lifecycle, and AZ
        # selection events in production logs. Bump to INFO/WARNING via
        # FORGE_LOG__LEVEL once the system is stable and the noise becomes
        # a cost concern.
        "level": "DEBUG",
        "json_indent": None,
    },
}

# Top-level keys whose values are nested settings dicts (not Settings fields).
# Each entry corresponds to a BaseSettings subclass with its own env-var prefix
# and its own _build_customise_sources binding. Anything NOT in this set is
# treated as a flat Settings field and flows into the top-level baseline.
# Failing to add a new nested class here means its defaults dict would leak
# into Settings as a stray field at construction time — and with
# extra="forbid" that surfaces as a loud ValidationError rather than a
# silent type mismatch.
_NESTED_SETTINGS_KEYS: frozenset[str] = frozenset({"database", "celery", "terraform", "log"})


def _check_unknown_env_vars(
    settings_instance: BaseSettings,
    *,
    allowed_nested_prefixes: frozenset[str] = frozenset(),
) -> None:
    """Raise ValueError if any FORGE_* env var doesn't map to a defined field.

    Pydantic-settings silently drops unknown env vars even when extra="forbid"
    — that flag only governs init kwargs and the resolved field dict, not raw
    env-source filtering. So we scan os.environ ourselves at validation time
    and surface typos like FORGE_PROT=8000 (instead of FORGE_PORT) as a
    ValidationError at config load.

    For the top-level Settings class, env vars belonging to a registered
    nested class (FORGE_DATABASE__*, FORGE_LOG__*, ...) are passed through —
    those are validated by the corresponding nested class's own check.
    """
    cfg = settings_instance.model_config
    prefix = cfg.get("env_prefix") or ""
    case_sensitive = bool(cfg.get("case_sensitive", False))

    fields = type(settings_instance).model_fields
    known = {(name if case_sensitive else name.upper()) for name in fields}
    allowed_nested = {(p if case_sensitive else p.upper()) for p in allowed_nested_prefixes}

    for raw_key in os.environ:
        key = raw_key if case_sensitive else raw_key.upper()
        norm_prefix = prefix if case_sensitive else prefix.upper()
        if not key.startswith(norm_prefix):
            continue
        stripped = key[len(norm_prefix) :]
        if not stripped:
            continue
        # Nested env vars use double-underscore as the namespace delimiter.
        # Defer them to the nested class's own validator.
        if "__" in stripped:
            ns = stripped.split("__", 1)[0]
            if ns in allowed_nested:
                continue
            raise ValueError(f"Unknown {prefix}* environment variable: {raw_key} (no nested namespace '{ns.lower()}')")
        if stripped not in known:
            raise ValueError(f"Unknown {prefix}* environment variable: {raw_key}")


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
        extra="forbid",
    )

    # Typed at load-time: a typo like FORGE_LOG__LEVEL=INF0 raises a
    # ValidationError here instead of reaching uvicorn.run(log_level="inf0")
    # and crashing at boot. The mode="before" validator on `level` below
    # uppercases the input first, so FORGE_LOG__LEVEL=debug, Debug, and
    # DEBUG all resolve to "DEBUG" — matches the historical behaviour
    # where logging.py did .upper() and __main__.py did .lower() at the
    # point of use.
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    # Pretty-prints JSON across multiple lines when set (useful for local
    # eyeballing). MUST stay None in production: log aggregators (CloudWatch,
    # Datadog, etc.) parse one JSON record per line and an indented record
    # spans many lines, breaking ingestion.
    json_indent: int | None = None

    @field_validator("level", mode="before")
    @classmethod
    def _upper_level(cls, v: object) -> object:
        # Normalise to upper-case before the Literal check so operators can
        # set FORGE_LOG__LEVEL=debug (matches Python's logging convention)
        # without tripping a validation error. Non-string inputs pass through
        # untouched so the Literal still rejects them with a clear error.
        if isinstance(v, str):
            return v.upper()
        return v

    @field_validator("json_indent")
    @classmethod
    def _indent_non_negative(cls, v: int | None) -> int | None:
        # json.dumps accepts any int but negative values produce a confusing
        # mix (no indent + leading newlines). Reject loud at config-load.
        if v is not None and v < 0:
            raise ValueError("json_indent must be None or >= 0")
        return v

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "LogSettings":
        _check_unknown_env_vars(self)
        return self

    settings_customise_sources = _build_customise_sources("log")


class CelerySettings(BaseSettings):
    """Celery / Redis broker settings.

    Local dev points broker_url at the docker-compose redis service.
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
        extra="forbid",
    )

    broker_url: str
    task_default_queue: str
    # Hard time limit in seconds — Celery SIGKILLs the worker child after
    # this. Long enough for `terraform apply` on a database to finish,
    # short enough to recover a stuck worker. Tune per environment via
    # FORGE_CELERY__TASK_TIME_LIMIT.
    task_time_limit: int
    # Soft time limit raises SoftTimeLimitExceeded inside the task so it
    # can clean up before the hard kill. Should be lower than task_time_limit.
    task_soft_time_limit: int

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "CelerySettings":
        _check_unknown_env_vars(self)
        return self

    settings_customise_sources = _build_customise_sources("celery")


class TerraformSettings(BaseSettings):
    """Terraform workspace / managed-resources settings.

    The worker materializes per-request Terraform workspaces under
    /tmp/forge-workspaces/ by copying the matching versioned package from
    packages_dir and rendering a backend.tf pointing at the S3 state bucket.

    managed_resources_bucket / managed_resources_region are blank in
    DEFAULT_SETTINGS so cloud envs must set
    FORGE_TERRAFORM__MANAGED_RESOURCES_BUCKET and
    FORGE_TERRAFORM__MANAGED_RESOURCES_REGION explicitly. packages_dir
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
        extra="forbid",
    )

    managed_resources_bucket: str
    managed_resources_region: str
    packages_dir: str
    binary: str

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "TerraformSettings":
        _check_unknown_env_vars(self)
        return self

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
        extra="forbid",
    )

    host: str
    port: int
    name: str
    user: str
    password: str
    ssl_mode: str
    schema: str  # type: ignore[assignment]  # shadows deprecated BaseModel.schema() — intentional, see warning filter at top of file

    # Connection pool — tune for Aurora Serverless v2 idle-connection behaviour.
    connect_timeout: int  # seconds to wait for TCP connect to the DB host
    pool_timeout: int  # seconds a caller waits when the pool is exhausted
    pool_recycle: int  # recycle interval (< Aurora idle timeout)

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "DatabaseSettings":
        _check_unknown_env_vars(self)
        return self

    settings_customise_sources = _build_customise_sources("database")

    @property
    def url(self) -> str:
        """asyncpg DSN — used by the async SQLAlchemy engine at runtime."""
        base = f"postgresql+asyncpg://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        if self.schema != "public":
            base += f"?server_settings[search_path]={self.schema}"
        return base

    @property
    def sync_url(self) -> str:
        """psycopg2 DSN — used by Alembic (sync) and the seed script."""
        base = f"postgresql+psycopg2://{self.user}:{self.password}@{self.host}:{self.port}/{self.name}"
        params = [f"sslmode={self.ssl_mode}"]
        if self.schema != "public":
            params.append(f"options=-csearch_path%3D{self.schema}")
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
        # Fails loud on unknown FORGE_* env vars (e.g. FORGE_PROT=8000)
        # instead of silently swallowing the typo.
        extra="forbid",
    )

    app_name: str
    environment: str
    host: str
    port: int
    reload: bool

    # Request handling — tune these together with the ALB and ECS stopTimeout.
    # request_timeout < ALB idle timeout (60 s default) — cancel slow handlers
    #   before the ALB returns a 504 to the client.
    # keepalive_timeout < ALB idle timeout — prevent the ALB from closing a
    #   connection that uvicorn still considers alive.
    # shutdown_timeout < ECS stopTimeout (30 s default) — drain in-flight
    #   requests before ECS sends SIGKILL.
    request_timeout: int
    keepalive_timeout: int
    shutdown_timeout: int

    # Nested classes own their own env-var loading and DEFAULT_SETTINGS lookup
    # via _build_customise_sources. They construct themselves from () here.
    database: DatabaseSettings = Field(default_factory=lambda: DatabaseSettings())  # type: ignore[call-arg]
    celery: CelerySettings = Field(default_factory=lambda: CelerySettings())  # type: ignore[call-arg]
    terraform: TerraformSettings = Field(default_factory=lambda: TerraformSettings())  # type: ignore[call-arg]
    log: LogSettings = Field(default_factory=lambda: LogSettings())  # type: ignore[call-arg]

    @model_validator(mode="after")
    def _reject_unknown_env_vars(self) -> "Settings":
        # Top-level FORGE_* env vars — anything with __ defers to the nested
        # class's own validator. So FORGE_DATABASE__HOST is allowed through
        # here and DatabaseSettings._reject_unknown_env_vars catches typos
        # like FORGE_DATABASE__HOSP. A bare FORGE_FOOBAR with no __ that
        # doesn't match a top-level field name raises here.
        _check_unknown_env_vars(self, allowed_nested_prefixes=_NESTED_SETTINGS_KEYS)
        return self

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
