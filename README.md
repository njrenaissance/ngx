# Forge — NGX Self-Service Infrastructure Provisioning Service

Forge is a small platform-engineering service that accepts validated requests
to provision AWS resources, queues them for asynchronous execution, and runs
the actual provisioning through Terraform. It is the deliverable for the NGX
Senior Platform Engineer code challenge.

The first PR (this one) ships the foundation: a minimal FastAPI service with
health endpoints, the full Docker/Compose local-dev stack, the AWS
infrastructure (VPC, ALB, ECS Fargate, ECR), and the CI/CD pipeline that
gets the service live on AWS. Subsequent PRs add the request lifecycle, the
Celery worker, the Terraform runner, and the policy engine.

- **Repo**: `njrenaissance/ngx`
- **Application package**: `src/forge/`
- **AWS resources**: prefixed `forge-<env>-` (e.g. `forge-dev-vpc`)

---

## Architecture at a glance

See [`docs/diagrams/NGX_Networkinig.drawio`](docs/diagrams/NGX_Networkinig.drawio)
for the network topology. The service runs as a single Fargate task behind an
ALB; egress flows through a single NAT gateway in `us-east-1a`. Persistence
(Aurora) and the worker queue (Redis/Celery) land in PR 2.

```
              ┌──────────┐
   Internet → │   ALB    │ (forge-dev-alb, HTTP :80)
              └────┬─────┘
                   │
                   ▼
              ┌──────────────────┐
              │  ECS Fargate     │ (forge-dev cluster + service)
              │  forge:<version> │ (image pulled from ECR)
              │  uvicorn :8000   │
              └──────────────────┘
```

The Forge container is built on every push to `main` and published to
Amazon ECR (`<account>.dkr.ecr.<region>.amazonaws.com/forge-<env>`). ECS
Fargate pulls from there.

---

## Local development

Requirements:
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- Docker Desktop or any Compose v2 runtime
- (Optional) Terraform 1.10+ if you intend to validate the AWS stack locally

### Run the unit tests

```sh
uv sync
uv run pytest -m unit -v
```

### Run the service with hot reload

```sh
uv run python -m forge
```

By default Forge listens on `0.0.0.0:8000`. Override via environment
variables (all prefixed `FORGE_`):

| Variable | Default | Purpose |
|---|---|---|
| `FORGE_APP_NAME` | `Forge` | Service identity in `/livez` response |
| `FORGE_ENVIRONMENT` | `dev` | Free-form environment label |
| `FORGE_LOG_LEVEL` | `INFO` | uvicorn + app log level |
| `FORGE_HOST` | `0.0.0.0` | Bind address |
| `FORGE_PORT` | `8000` | Bind port (both host and container) |
| `FORGE_RELOAD` | `false` | `true` enables uvicorn auto-reload (dev only) |

Copy `.env.example` to `.env` and edit; both `uv run python -m forge` and
Docker Compose will pick the values up.

### Run the service in Docker Compose

```sh
docker compose up -d
curl http://localhost:8000/livez
```

### Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | 307 redirect to `/docs` (Swagger UI) |
| `GET` | `/livez` | Liveness probe; returns `{status, message, version}` |
| `GET` | `/readyz` | Readiness probe; ALB target-group health check |
| `GET` | `/docs` | OpenAPI Swagger UI |
| `GET` | `/openapi.json` | OpenAPI 3 schema |

### Run the integration tests (end-to-end against a live container)

```sh
uv run pytest -m integration -v
```

The `pytest-docker` fixture brings up the Compose stack, waits for `/livez`
to respond, runs the suite, and tears the stack down on session exit.

---

## GitHub Repository Setup

The CI workflows in [`.github/workflows/`](.github/workflows/) depend on the
following repository configuration. If you fork or reproduce the project,
configure these once under **Settings → Secrets and variables → Actions**.

### Secrets

| Secret | Value | Used by |
|---|---|---|
| `AWS_ACCESS_KEY_ID` | Access key for the `ngx-deployer` IAM user | `terraform.yml`, `build-container.yml`, `deploy.yml` |
| `AWS_SECRET_ACCESS_KEY` | Paired secret key | same |

> Note: the project deliberately uses long-lived access keys for now to
> reduce setup friction. A migration to GitHub OIDC with a scoped IAM role
> is planned for a follow-up PR and would replace both secrets above with
> a single `AWS_ROLE_ARN`.

### Variables

| Variable | Recommended value | Used by | Notes |
|---|---|---|---|
| `AWS_REGION` | `us-east-1` | all workflows | Matches the `var.aws_region` default in Terraform |
| `ECR_REPOSITORY` | `forge-dev` | `build-container.yml`, `terraform.yml` | Matches `aws_ecr_repository.forge.name` |
| `ECS_CLUSTER` | `forge-dev` | `deploy.yml` | Matches `aws_ecs_cluster.main.name` |
| `ECS_SERVICE` | `forge-dev` | `deploy.yml` | Matches `aws_ecs_service.app.name` |

### Environment

Create a GitHub **Environment** named `production` and add at least one
required reviewer:

- **Settings → Environments → New environment → `production`**
- Add a **required reviewer** (yourself, or another repo admin)

The `terraform.yml` `apply` job targets this environment, so every
`terraform apply` against AWS waits for a human approval before proceeding.

---

## CI/CD pipeline

Four GitHub Actions workflows orchestrate the delivery:

### 1. `format-lint.yml` + `unit-tests.yml` + `ci.yml`

Already present. Runs on every PR against main:
- `ruff format --check` and `ruff check` against `src/` and `tests/`
- `mypy src/`
- `pytest -m unit` with coverage

These have no AWS dependencies.

### 2. `build-container.yml`

Triggers on push to `main` (and `workflow_dispatch`). Builds the Forge image
and pushes to Amazon ECR with three tag styles:

- `:latest` (always overwrites)
- `:<pyproject-version>` (e.g. `:0.1.0`)
- `:<short-sha>` (commit SHA, immutable per build)

Skips file paths that don't affect the image (`docs/`, `*.md`,
`infrastructure/`, `.claude/`).

To pull the image locally for inspection:

```sh
aws ecr get-login-password --region us-east-1 \
  | docker login --username AWS --password-stdin <account>.dkr.ecr.us-east-1.amazonaws.com
docker pull <account>.dkr.ecr.us-east-1.amazonaws.com/forge-dev:latest
```

### 3. `terraform.yml`

Triggers on PR (`plan` only) and push to `main` (`plan` then `apply`).

| Job | When | What |
|---|---|---|
| `plan` | PR + main | `fmt -check` → `init` → `validate` → `plan`, posts the plan as a PR comment, uploads `tfplan` artifact |
| `apply` | main only | Requires `production` environment approval, then `terraform apply tfplan` |

`fmt`, `validate`, and `plan` run as steps in the same job to avoid paying
runner-spinup overhead twice. `apply` is a separate job *only* because GitHub
Environment approval gates are job-scoped — there's no way to pause a single
step waiting for a reviewer.

The plan is generated against `app_image = ECR:latest`. Terraform never
hand-rolls an image-version bump; it always references `:latest` and lets
the deploy workflow force ECS to pick up new content.

### 4. `deploy.yml`

Triggers automatically after `build-container.yml` finishes successfully on
`main`. Runs `aws ecs update-service --force-new-deployment`, then blocks
on `aws ecs wait services-stable` so the workflow only succeeds once the
new tasks are healthy.

### End-to-end flow for a typical change

```text
   ┌─ Open PR ─────────────────────────────────────────────┐
   │  format-lint.yml ✓ unit-tests.yml ✓                    │
   │  terraform.yml: fmt+validate+plan → comment on PR ✓    │
   └────────────────────────────────────────────────────────┘
            │
            │ (review, approve, merge to main)
            ▼
   ┌─ Push to main ────────────────────────────────────────┐
   │  build-container.yml → pushes :latest + :version to    │
   │                        GHCR and ECR                    │
   │  terraform.yml: plan + apply (production gate ⏸)       │
   │  deploy.yml: aws ecs update-service                    │
   │              wait services-stable                      │
   └────────────────────────────────────────────────────────┘
            │
            ▼
   curl http://<alb-dns>/livez  → 200 OK
```

---

## Infrastructure layout

```
infrastructure/
├── bootstrap/          # Terraform state backend — RUN ONCE manually
│   ├── README.md       # Operating procedure
│   ├── main.tf         # S3 bucket + DynamoDB lock table
│   └── ...
├── dev/                # The dev environment stack — CI-managed
│   ├── backend.tf      # S3 backend with use_lockfile = true
│   ├── main.tf         # VPC, ALB, ECS, ECR, IAM, CloudWatch
│   ├── providers.tf
│   ├── variables.tf
│   └── terraform.tfvars.example
└── policies/
    └── ngx-deployer-policy.json   # IAM policy for the ngx-deployer user
```

### First-time setup (one-time)

If you're starting from a fresh AWS account:

1. **Create the `ngx-deployer` IAM user** (or rename to taste) and attach
   the policy from
   [`infrastructure/policies/ngx-deployer-policy.json`](infrastructure/policies/ngx-deployer-policy.json).
2. **Run the bootstrap** to create the Terraform state backend:

   ```sh
   terraform -chdir=infrastructure/bootstrap init
   terraform -chdir=infrastructure/bootstrap apply
   ```

   This creates `forge-tfstate-<account-id>` (S3) and `forge-tfstate-lock`
   (DynamoDB). It runs once forever. See
   [`infrastructure/bootstrap/README.md`](infrastructure/bootstrap/README.md).

3. **Update `infrastructure/dev/backend.tf`** to reference the bucket name
   that the bootstrap printed (if your account ID differs from the one
   currently hardcoded).
4. **Configure GitHub secrets/variables/environments** as described above.
5. **Open a PR** with any change to `infrastructure/dev/**` to confirm the
   pipeline runs end-to-end.

### Day-to-day infrastructure changes

Edit any file in `infrastructure/dev/`, open a PR, review the plan comment,
merge. The pipeline does the rest.

---

## Engineering conventions

See [`CLAUDE.md`](CLAUDE.md) for the working agreement. Key rules:

- `uv` for all Python tooling (`uv sync`, `uv run pytest`, etc.)
- Pre-commit hooks enforced — install with
  `uv run pre-commit install --hook-type pre-commit --hook-type pre-push`
- Conventional commits (`feat:`, `fix:`, `chore:`, …) with a
  `Co-Authored-By` trailer when AI-assisted
- SemVer in `pyproject.toml`; bump on any `src/forge/**` change
- Diagrams under `docs/diagrams/` must stay in sync with the Terraform stack
- Bootstrap is run-once-ever and is NOT touched by CI
