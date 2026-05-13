"""Tests for the Forge settings layer (src/forge/config.py).

Focus: the hardening guarantees added in issue #7 — typed log level,
strict env-var matching (extra="forbid"), and the safer FORGE_HOST default.

These tests construct Settings()/LogSettings() directly to bypass the
@lru_cache on get_settings() and observe the validation behaviour for a
specific env-var state per test.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any, cast

import pytest
from pydantic import ValidationError

from forge.config import (
    DEFAULT_SETTINGS,
    CelerySettings,
    DatabaseSettings,
    LogSettings,
    Settings,
    TerraformSettings,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_forge_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every FORGE_* env var so the developer's local .env or shell
    can't leak into these tests. Each test then sets only the vars it wants."""
    for key in list(os.environ):
        if key.startswith("FORGE_") or key == "FORGE":
            monkeypatch.delenv(key, raising=False)
    yield


def _build_settings(**_init_kwargs: Any) -> Settings:
    # Settings() picks up DEFAULT_SETTINGS via its custom source chain; mypy
    # doesn't see those values so we cast through Any.
    return cast(Settings, Settings())  # type: ignore[call-arg]


class TestLogLevelTyped:
    def test_invalid_log_level_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_LOG__LEVEL", "INF0")  # zero, not letter O
        with pytest.raises(ValidationError):
            LogSettings()  # type: ignore[call-arg]

    @pytest.mark.parametrize("level", ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
    def test_valid_log_levels_accepted(self, monkeypatch: pytest.MonkeyPatch, level: str) -> None:
        monkeypatch.setenv("FORGE_LOG__LEVEL", level)
        log = LogSettings()  # type: ignore[call-arg]
        assert log.level == level

    @pytest.mark.parametrize(
        "input_value,expected",
        [
            ("debug", "DEBUG"),
            ("info", "INFO"),
            ("Warning", "WARNING"),
            ("eRrOr", "ERROR"),
            ("critical", "CRITICAL"),
        ],
    )
    def test_log_level_value_is_case_insensitive(
        self, monkeypatch: pytest.MonkeyPatch, input_value: str, expected: str
    ) -> None:
        # The mode="before" validator uppercases the input so FORGE_LOG__LEVEL=debug
        # (matches Python's logging convention) doesn't trip the Literal check.
        monkeypatch.setenv("FORGE_LOG__LEVEL", input_value)
        assert LogSettings().level == expected  # type: ignore[call-arg]


class TestExtraForbid:
    def test_unknown_top_level_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Typo: FORGE_PROT instead of FORGE_PORT.
        monkeypatch.setenv("FORGE_PROT", "8000")
        with pytest.raises(ValidationError):
            _build_settings()

    def test_unknown_nested_database_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Typo: FORGE_DATABASE__HOSP instead of FORGE_DATABASE__HOST.
        monkeypatch.setenv("FORGE_DATABASE__HOSP", "db.example.com")
        with pytest.raises(ValidationError):
            DatabaseSettings()  # type: ignore[call-arg]

    def test_unknown_nested_celery_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_CELERY__BORKER_URL", "redis://nope")
        with pytest.raises(ValidationError):
            CelerySettings()  # type: ignore[call-arg]

    def test_unknown_nested_terraform_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_TERRAFORM__BINERY", "/usr/bin/terraform")
        with pytest.raises(ValidationError):
            TerraformSettings()  # type: ignore[call-arg]

    def test_unknown_nested_log_env_var_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_LOG__LEVL", "DEBUG")
        with pytest.raises(ValidationError):
            LogSettings()  # type: ignore[call-arg]


class TestHostDefault:
    def test_host_defaults_to_loopback(self) -> None:
        # No FORGE_HOST set — _clean_forge_env removed any leak.
        s = _build_settings()
        assert s.host == "127.0.0.1"
        assert DEFAULT_SETTINGS["host"] == "127.0.0.1"

    def test_host_env_override_takes_effect(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FORGE_HOST", "0.0.0.0")
        s = _build_settings()
        assert s.host == "0.0.0.0"

    def test_uppercase_env_var_maps_to_lowercase_field(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Guard against an accidental case_sensitive=True on SettingsConfigDict
        # which would break FORGE_HOST -> host mapping.
        monkeypatch.setenv("FORGE_HOST", "10.0.0.5")
        monkeypatch.setenv("FORGE_PORT", "9001")
        s = _build_settings()
        assert s.host == "10.0.0.5"
        assert s.port == 9001
