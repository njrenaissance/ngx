#!/usr/bin/env python3
"""Deterministic stand-in for the `terraform` CLI used by the test suite.

Why this exists: TerraformRunner shells out via subprocess. To exercise the
init/plan/apply lifecycle (and the SPEC Appendix B rule 4 plan-required
guard) end-to-end without needing a real AWS account or a real terraform
binary in CI, tests point FORGE_TERRAFORM__BINARY at this script.

Subcommands implemented:
  init         — prints "Initializing..." and exits 0.
  plan         — accepts -out=PATH; writes a deterministic JSON blob to
                 PATH; prints sample output containing an ARN/account/region
                 so the integration suite can assert sanitization.
  apply PATH   — verifies PATH exists; prints sample output; exits 0.
  output -json — prints a fixture-defined outputs object.

Failure injection: set FORGE_FAKE_TF_FAIL to one of {init, plan, apply, output}
to make that subcommand exit 2 with cloud-coordinate-laden stderr. Used by
TerraformRunner unit tests to verify sanitization on the failure path.

Any other behaviour (real arg parsing, refresh, validate, etc.) is
intentionally absent — if a test path needs something this fake doesn't
do, that's a signal to extend the fake, not to skip the test.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Cloud coordinates the failure-mode stderr embeds. The runner's sanitizer
# must scrub all three categories before the message is persisted into
# APPLY_JOB.log_sanitized; the fact that the fake emits them at all is the
# whole point — it lets the sanitizer be tested against real-shaped output.
_FAKE_ARN = "arn:aws:s3:::fake-bucket-not-real"
_FAKE_ACCOUNT = "123456789012"
_FAKE_REGION = "us-east-1"


def _maybe_fail(stage: str) -> None:
    if os.environ.get("FORGE_FAKE_TF_FAIL") == stage:
        sys.stderr.write(f"Error in {stage}: failed creating {_FAKE_ARN} in account {_FAKE_ACCOUNT} ({_FAKE_REGION})\n")
        sys.exit(2)


def _cmd_init() -> int:
    _maybe_fail("init")
    sys.stdout.write("Initializing the backend...\nTerraform has been successfully initialized!\n")
    return 0


def _cmd_plan(args: list[str]) -> int:
    _maybe_fail("plan")
    out_path: str | None = None
    for a in args:
        if a.startswith("-out="):
            out_path = a.removeprefix("-out=")
    if out_path is None:
        sys.stderr.write("fake terraform: plan requires -out=PATH\n")
        return 1
    Path(out_path).write_text(json.dumps({"plan": "ok", "fake": True}, sort_keys=True))
    # Embed cloud coordinates in stdout so integration tests can assert
    # the runner's sanitization actually fires on real-shaped output.
    sys.stdout.write(
        f"Terraform will perform the following actions in {_FAKE_REGION} for account {_FAKE_ACCOUNT}.\n"
        f"  + create {_FAKE_ARN}\nPlan: 1 to add, 0 to change, 0 to destroy.\n"
    )
    return 0


def _cmd_apply(args: list[str]) -> int:
    _maybe_fail("apply")
    # The plan path is the final positional arg from TerraformRunner.apply.
    positional = [a for a in args if not a.startswith("-")]
    if not positional:
        sys.stderr.write("fake terraform: apply requires a plan path\n")
        return 1
    plan_path = Path(positional[-1])
    if not plan_path.is_file():
        sys.stderr.write(f"fake terraform: plan file not found at {plan_path}\n")
        return 1
    sys.stdout.write(
        f"Apply complete in {_FAKE_REGION} for account {_FAKE_ACCOUNT}.\nResources: 1 added, 0 changed, 0 destroyed.\n"
    )
    return 0


def _cmd_output(args: list[str]) -> int:
    _maybe_fail("output")
    # Only -json shape is implemented; raw `terraform output` text isn't used
    # by the runner.
    if "-json" not in args:
        sys.stderr.write("fake terraform: output without -json is not implemented\n")
        return 1
    payload = {"endpoint": {"value": "fake.example.internal", "type": "string", "sensitive": False}}
    sys.stdout.write(json.dumps(payload))
    return 0


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("fake terraform: subcommand required\n")
        return 1
    sub, *rest = argv[1:]
    if sub == "init":
        return _cmd_init()
    if sub == "plan":
        return _cmd_plan(rest)
    if sub == "apply":
        return _cmd_apply(rest)
    if sub == "output":
        return _cmd_output(rest)
    sys.stderr.write(f"fake terraform: unknown subcommand {sub!r}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
