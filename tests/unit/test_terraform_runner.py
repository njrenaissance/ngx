"""Unit tests for TerraformRunner — SPEC Appendix B rules 1 and 4.

The runner is exercised against a real Python subprocess invocation of a
deterministic fake terraform script (tests/_fake_terraform/fake_terraform.py).
We don't mock subprocess.run because the contract under test IS the
subprocess invocation — capture, returncode, sanitization, and the
plan-required guard.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from forge.workers.terraform_runner import (
    PlanRequiredError,
    TerraformExecutionError,
    TerraformRunner,
    _sanitize,
)

pytestmark = pytest.mark.unit

FAKE_TF = Path(__file__).resolve().parents[1] / "_fake_terraform" / "fake_terraform.py"


@pytest.fixture
def runner() -> TerraformRunner:
    """A runner pointed at the fake terraform script.

    `sys.executable` ensures the same interpreter the tests are running
    under is used to invoke the fake — avoids PATH / venv mismatches when
    the suite runs under uv.
    """
    return TerraformRunner(binary=f"{sys.executable} {FAKE_TF}")


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


# ─── sanitizer ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected_present", "expected_absent"),
    [
        ("created arn:aws:s3:::my-bucket", "***ARN***", "my-bucket"),
        ("account 123456789012 owns this", "***ACCOUNT***", "123456789012"),
        ("region us-east-1 is the home", "***REGION***", "us-east-1"),
        ("AZ us-east-1a inside region", "***REGION***", "us-east-1a"),
        ("multi: arn:aws:rds:us-east-1:123456789012:db", "***ARN***", "123456789012"),
    ],
)
def test_sanitize_strips_cloud_coordinates(raw: str, expected_present: str, expected_absent: str) -> None:
    out = _sanitize(raw)
    assert expected_present in out
    assert expected_absent not in out


def test_sanitize_idempotent() -> None:
    once = _sanitize("arn:aws:s3:::b in us-east-1 by 123456789012")
    twice = _sanitize(once)
    assert once == twice


def test_sanitize_leaves_unrelated_numbers_alone() -> None:
    # 11 digits — not a 12-digit account id; must stay verbatim.
    assert _sanitize("port 12345678901 ok") == "port 12345678901 ok"


def test_sanitize_does_not_eat_substrings_of_unrelated_tokens() -> None:
    # "build-east-1-runner" contains substring matching the region-suffix
    # form but is bounded by non-region prefix; word-boundary anchors should
    # leave it intact.
    assert _sanitize("hostname build-east-1-runner") == "hostname build-east-1-runner"


# ─── plan-required guard (SPEC Appendix B rule 4) ─────────────────────────────


def test_apply_without_any_plan_raises(runner: TerraformRunner, workdir: Path) -> None:
    with pytest.raises(PlanRequiredError):
        runner.apply(workdir, workdir / "tfplan")


def test_apply_with_unrecorded_plan_path_raises(runner: TerraformRunner, workdir: Path) -> None:
    """A plan file on disk that this runner did NOT produce must not satisfy the guard.

    Defends against a stale leftover from a previous run sneaking through
    the apply path. The runner's in-memory provenance set is the source
    of truth; the on-disk file is necessary but not sufficient.
    """
    rogue_plan = workdir / "tfplan"
    rogue_plan.write_text("not produced by this runner")
    with pytest.raises(PlanRequiredError):
        runner.apply(workdir, rogue_plan)


def test_apply_with_recorded_plan_but_missing_file_raises(runner: TerraformRunner, workdir: Path) -> None:
    """If plan() recorded the path but the file got deleted, apply must refuse."""
    plan_path = runner.plan(workdir)
    plan_path.unlink()
    with pytest.raises(PlanRequiredError):
        runner.apply(workdir, plan_path)


# ─── happy-path subprocess capture ────────────────────────────────────────────


def test_init_completes_successfully(runner: TerraformRunner, workdir: Path) -> None:
    result = runner.init(workdir)
    assert result.returncode == 0
    assert "Initializing" in result.stdout


def test_plan_writes_plan_file_and_returns_path(runner: TerraformRunner, workdir: Path) -> None:
    plan_path = runner.plan(workdir)
    assert plan_path.is_file()
    # The fake writes a small JSON blob; assert it's non-empty.
    assert plan_path.stat().st_size > 0


def test_apply_after_plan_returns_parsed_outputs(runner: TerraformRunner, workdir: Path) -> None:
    runner.init(workdir)
    plan_path = runner.plan(workdir)
    outputs = runner.apply(workdir, plan_path)
    # Fake terraform always emits a single "endpoint" output.
    assert "endpoint" in outputs
    assert outputs["endpoint"]["value"] == "fake.example.internal"


# ─── failure paths ────────────────────────────────────────────────────────────


def test_runner_raises_on_nonzero_with_sanitized_stderr(
    runner: TerraformRunner, workdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fake-fail mode: apply exits non-zero with cloud-coordinate-laden stderr.

    The TerraformExecutionError carries already-sanitized stderr — callers
    can persist it directly into APPLY_JOB.log_sanitized without re-scrubbing.
    """
    monkeypatch.setenv("FORGE_FAKE_TF_FAIL", "apply")
    runner.init(workdir)
    plan_path = runner.plan(workdir)
    with pytest.raises(TerraformExecutionError) as excinfo:
        runner.apply(workdir, plan_path)
    assert excinfo.value.stage == "apply"
    assert excinfo.value.returncode != 0
    assert "arn:aws:" not in excinfo.value.sanitized_stderr
    # The fake-failure stderr embeds a 12-digit account id; assert it's gone.
    assert "123456789012" not in excinfo.value.sanitized_stderr


def test_runner_rejects_empty_binary() -> None:
    with pytest.raises(ValueError):
        TerraformRunner(binary="")
