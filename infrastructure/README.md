# Infrastructure

Terraform code for the NGX platform. Managed via GitHub Actions — see `.github/workflows/terraform.yml`.

## Directory layout

| Directory | Purpose |
|-----------|---------|
| `bootstrap/` | One-time manual setup: S3 backend bucket, DynamoDB lock table, OIDC provider, deploy IAM role. **Never touched by CI.** See `bootstrap/README.md`. |
| `dev/` | Root module for the `dev` environment. This is what CI plans and applies. |
| `modules/` | Reusable child modules (`ecr`, `kms`, …). Each ships unit tests under `modules/<name>/tests/`. |
| `policies/` | IAM policy JSON documents referenced by modules. |

## CI gates (`.github/workflows/terraform.yml`)

Every PR and push to `main` that touches `infrastructure/` or the workflow file runs the `Plan` job, which executes the following quality gates **in order**:

| Gate | Tool | Failure condition |
|------|------|-------------------|
| Format check | `terraform fmt -check -recursive` | Any file not formatted |
| Validation | `terraform validate` | Invalid HCL / provider schema errors |
| Lint | **tflint** v0.54.0 + AWS ruleset v0.38.0 | Any finding (all severities) |
| Security scan | **checkov** (bridgecrewio/checkov-action@v12) | HIGH or CRITICAL findings; MEDIUM/LOW are reported but non-blocking |
| Module tests | `terraform test` per module | Any test failure |
| Plan | `terraform plan` | Plan errors |

`Apply` runs only on push to `main`, is gated by the `production` GitHub Environment (human approval required), and only starts after `Plan` passes all gates above.

## Running tflint locally

```bash
# From repo root
cd infrastructure
tflint --init --config .tflint.hcl
tflint --recursive --config .tflint.hcl
```

## Running checkov locally

```bash
uv run --with checkov checkov -d infrastructure/ --framework terraform
```
