"""Thin wrapper around the `terraform` CLI for the provisioning worker.

The worker shells out for init/plan/apply/output rather than calling AWS APIs
directly because the SPEC's plan-then-apply contract (Appendix B rule 4) is
defined in terms of the terraform CLI's saved-plan file. Anything that
bypasses the CLI also bypasses the saved-plan guarantee.

Hard rules enforced here:
  - apply() refuses to run unless plan() previously produced the saved plan
    file in this process. Both an in-memory provenance set (workdir, plan)
    AND an on-disk file check must pass — either alone is insufficient
    (a stale leftover file from a previous run is not the same as "we
    just planned this"; an in-memory entry without the file means someone
    deleted it). See SPEC Appendix B rule 4.
  - All stdout/stderr returned to callers is run through _sanitize() so
    cloud coordinates (ARNs, 12-digit account IDs, AWS region/AZ codes)
    never leak into APPLY_JOB.log_sanitized or DEPLOYMENT.last_error.
    See SPEC Appendix B rule 1.

The binary path is `settings.terraform.binary` (default "terraform"). Tests
override via FORGE_TERRAFORM__BINARY to point at tests/_fake_terraform/
fake_terraform.py — accepts a shell-style command string which we split
with shlex so "python /path/to/fake.py" works without shell=True.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from forge.config import settings
from forge.logging import get_logger

logger = get_logger(__name__)


class TerraformRunnerError(Exception):
    """Base for runner-raised errors. Always carries pre-sanitized text."""


class PlanRequiredError(TerraformRunnerError):
    """apply() called without a saved plan produced by an earlier plan().

    Enforces SPEC Appendix B rule 4. Surfaces as a structural failure to the
    provision_resource task, which marks the APPLY_JOB failed without retry.
    """


class TerraformExecutionError(TerraformRunnerError):
    """A terraform CLI invocation exited non-zero.

    Attributes:
        stage: Which stage failed ("init" / "plan" / "apply" / "output" / "destroy").
        returncode: Process exit code.
        sanitized_stderr: Combined stderr (plus any sanitized stdout context),
            already run through _sanitize() — safe to persist verbatim.
    """

    def __init__(self, stage: str, returncode: int, sanitized_stderr: str) -> None:
        super().__init__(f"terraform {stage} failed (rc={returncode}): {sanitized_stderr}")
        self.stage = stage
        self.returncode = returncode
        self.sanitized_stderr = sanitized_stderr


@dataclass(frozen=True)
class CompletedRun:
    """Result of a single terraform invocation (post-sanitization)."""

    stdout: str
    stderr: str
    returncode: int


# ─── sanitization ─────────────────────────────────────────────────────────────
#
# Three patterns cover the cloud coordinates SPEC Appendix B rule 1 forbids:
#   1. ARNs   — arn:aws:<service>:<region>:<account>:<resource>
#   2. Account IDs — bare 12-digit integers
#   3. Region / AZ codes — e.g. us-east-1, us-east-1a
#
# Order matters: scrub ARNs first (they contain account IDs and regions; if we
# scrubbed those first we'd still leave the ARN's "arn:aws:..." prefix and a
# partially-rewritten suffix that's harder to reason about).

_ARN_RE = re.compile(r"arn:aws[\w-]*:[\w\-]*:[\w\-]*:[\w\-]*:[^\s\"',]+")
_ACCOUNT_ID_RE = re.compile(r"\b\d{12}\b")
# AWS region codes look like <area>-<direction>-<digit>, with an optional AZ
# letter suffix. Anchored on word boundaries so we don't eat substrings of
# unrelated tokens (e.g. "build-east-1-runner" stays intact because the
# leading boundary fails on "build-").
_REGION_RE = re.compile(
    r"\b(?:us|eu|ap|sa|ca|me|af|cn)-(?:east|west|north|south|central|northeast|northwest|southeast|southwest)-\d[a-z]?\b"
)


def _sanitize(text: str) -> str:
    """Strip cloud coordinates from CLI output before persisting or returning.

    Idempotent — running on already-sanitized text is a no-op. The replacement
    tokens are deliberately distinguishable so a future operator reading the
    log can see a redaction happened without guessing what was there.
    """
    if not text:
        return text
    # Replace with ***ARN*** (not "arn:aws:***") so the literal "arn:aws:"
    # substring never appears in sanitized output. SPEC Appendix B rule 1
    # forbids ARNs in API responses; an unambiguous redaction marker means
    # callers can grep for "arn:aws:" as a regression check.
    text = _ARN_RE.sub("***ARN***", text)
    text = _ACCOUNT_ID_RE.sub("***ACCOUNT***", text)
    text = _REGION_RE.sub("***REGION***", text)
    return text


class TerraformRunner:
    """Per-task terraform CLI driver.

    Instantiate one runner per provision_resource task — the in-memory
    plan-provenance set is instance-scoped so a stray plan from a previous
    task can never be silently re-applied across task boundaries.
    """

    def __init__(self, binary: str | None = None) -> None:
        # `binary` is a shell-style command string — splits on spaces so tests
        # can pass "python /path/to/fake.py" without setting shell=True. The
        # split list becomes the leading argv elements; per-method args extend it.
        # posix=False keeps Windows backslashes intact (POSIX mode treats `\`
        # as an escape char and would mangle "C:\Users\..\python.exe").
        cmd_str = binary if binary is not None else settings.terraform.binary
        self._argv_prefix: list[str] = shlex.split(cmd_str, posix=False)
        if not self._argv_prefix:
            raise ValueError("terraform binary must be a non-empty command")
        # In-memory provenance: (workdir, plan_path) tuples produced by THIS
        # runner instance. apply() must find its plan in here AND on disk.
        self._plans_produced: set[tuple[Path, Path]] = set()
        # Cumulative sanitized log of every successful subprocess invocation
        # this runner has issued. provision_resource reads this via
        # cumulative_log() and persists it as APPLY_JOB.log_sanitized so the
        # audit trail captures the full init+plan+apply+output stdout, not
        # just one stage. Failures don't append here — TerraformExecutionError
        # already carries the sanitized stderr separately.
        self._log_chunks: list[str] = []

    def init(self, workdir: Path) -> CompletedRun:
        """`terraform init` — downloads providers, configures backend."""
        return self._run("init", workdir, ["init", "-input=false", "-no-color"])

    def plan(self, workdir: Path) -> Path:
        """`terraform plan -out=tfplan` — returns the saved plan path.

        The path is recorded in the runner's provenance set so a later
        apply() can verify it came from this runner (and not from a stale
        previous workspace state).
        """
        plan_path = (workdir / "tfplan").resolve()
        self._run("plan", workdir, ["plan", "-input=false", "-no-color", "-out=tfplan"])
        # Record after success — a plan that never ran shouldn't grant a
        # later apply() a free pass.
        self._plans_produced.add((workdir.resolve(), plan_path))
        return plan_path

    def apply(self, workdir: Path, plan_path: Path) -> dict:
        """`terraform apply <plan_path>` followed by `terraform output -json`.

        Hard guards before any terraform invocation:
          - (workdir, plan_path) MUST be in this runner's provenance set
            (i.e. this runner produced the plan via plan() above).
          - plan_path MUST exist on disk (catches the race where someone
            deleted the file between plan and apply).

        Returns the parsed `terraform output -json` mapping (output_name -> dict
        with `value`, `type`, etc., per terraform's output format).
        """
        resolved_workdir = workdir.resolve()
        resolved_plan = plan_path.resolve()
        if (resolved_workdir, resolved_plan) not in self._plans_produced:
            raise PlanRequiredError(
                f"apply refused: no recorded plan for workdir={resolved_workdir} plan={resolved_plan}. "
                "Call plan() first (SPEC Appendix B rule 4)."
            )
        if not resolved_plan.is_file():
            raise PlanRequiredError(
                f"apply refused: plan file missing on disk at {resolved_plan} (SPEC Appendix B rule 4)."
            )

        # Pass the plan path as the final positional argument. -auto-approve
        # is required when applying a saved plan but is also implicit in
        # newer Terraform versions; included for clarity and to remain
        # compatible across the 1.5+ range we support.
        self._run("apply", workdir, ["apply", "-input=false", "-no-color", "-auto-approve", str(resolved_plan)])
        outputs_run = self._run("output", workdir, ["output", "-json"])
        try:
            parsed: dict = json.loads(outputs_run.stdout)
            return parsed
        except json.JSONDecodeError as exc:
            # Sanitize before raising — the message bubbles up into the
            # provision_resource failure path, which persists it.
            raise TerraformExecutionError(
                stage="output",
                returncode=outputs_run.returncode,
                sanitized_stderr=_sanitize(f"terraform output -json was not valid JSON: {exc}"),
            ) from exc

    def destroy(self, workdir: Path, plan_path: Path) -> CompletedRun:
        """`terraform apply <destroy-plan>` — same plan-required guard.

        Kept thin in this PR; the destroy task that drives it end-to-end
        arrives in a follow-up. The guard is here so a future caller can't
        accidentally bypass plan-then-apply on the destroy side.
        """
        resolved_workdir = workdir.resolve()
        resolved_plan = plan_path.resolve()
        if (resolved_workdir, resolved_plan) not in self._plans_produced:
            raise PlanRequiredError(
                f"destroy refused: no recorded plan for workdir={resolved_workdir} plan={resolved_plan}. "
                "Call plan() (with -destroy) first."
            )
        if not resolved_plan.is_file():
            raise PlanRequiredError(f"destroy refused: plan file missing on disk at {resolved_plan}.")
        return self._run(
            "destroy", workdir, ["apply", "-input=false", "-no-color", "-auto-approve", str(resolved_plan)]
        )

    def _run(self, stage: str, workdir: Path, args: list[str]) -> CompletedRun:
        """Invoke the terraform binary; sanitize output; raise on non-zero rc."""
        argv = [*self._argv_prefix, *args]
        logger.debug(
            "terraform invocation",
            extra={"stage": stage, "workdir": str(workdir), "argv_tail": args},
        )
        proc = subprocess.run(
            argv,
            cwd=str(workdir),
            capture_output=True,
            text=True,
            check=False,
        )
        sanitized_stdout = _sanitize(proc.stdout or "")
        sanitized_stderr = _sanitize(proc.stderr or "")
        if proc.returncode != 0:
            # Combine stderr with a snippet of stdout so the persisted error
            # has enough context to debug, but keep stderr first since that's
            # where terraform writes diagnostic detail.
            combined = sanitized_stderr or sanitized_stdout or "(no output)"
            raise TerraformExecutionError(stage=stage, returncode=proc.returncode, sanitized_stderr=combined)
        # Append a stage banner + sanitized stdout to the cumulative log so
        # the caller can persist a full audit trail without re-running the
        # subprocess. We deliberately store sanitized_stdout (not raw) — the
        # audit log surfaces directly into APPLY_JOB.log_sanitized.
        self._log_chunks.append(f"$ terraform {stage}\n{sanitized_stdout}")
        return CompletedRun(stdout=sanitized_stdout, stderr=sanitized_stderr, returncode=proc.returncode)

    def cumulative_log(self) -> str:
        """Sanitized stdout from every successful invocation, in order.

        Used by provision_resource to populate APPLY_JOB.log_sanitized with
        the full init+plan+apply+output trail. Failed invocations don't
        appear here — their sanitized stderr lives on the raised
        TerraformExecutionError instead.
        """
        return "\n\n".join(self._log_chunks)
