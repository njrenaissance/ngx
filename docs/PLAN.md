# Plan: NGX Infrastructure Provisioning Service

## Context

Senior Platform Engineer code challenge. Build an Internal Developer Platform automation service ("Infrastructure Provisioning Service") on AWS ECS Fargate, deployed via Terraform + GitHub Actions, with strong AI-assisted dev workflow visible in PRs/commits. Must be **reachable during the live interview** and demonstrate platform-as-a-product thinking.

**Scoring (from PDF):** 30% service · 25% Terraform · 20% AI workflow · 15% CI/CD · 10% docs.

**Locked decisions:**
- Service: self-service API to provision AWS resources (S3 buckets first; expand later)
- Compute: ECS Fargate behind ALB (existing `pyproject.toml` already commits to FastAPI + asyncpg + OTel)
- Digging Deeper: **Option 1 — Complex Terraform** (KMS CMK, Aurora Serverless v2, autoscaling, IaC checks in CI)
- Approach: **MVP first, then iterate** — observability and the heavier Option-1 pieces come in iteration phase, not MVP
- **Execution model: API enqueues to Celery/Redis; worker shells out to the pinned `terraform` binary.** Per-request workdir + per-request remote state at `s3://ngx-tfstate/provisioned/{resource_type}/{request_id}.tfstate`. Jinja templates per resource type at `src/ngx/templates/*.tf.j2`. No Python "Terraform SDK" — the candidates (`python-terraform`, `libterraform`, CDKTF) all wrap or still require the binary; `tfexec` is Go-only. Shelling out with `-json` output streamed into structlog is the canonical pattern (Atlantis, Terragrunt, TFC runners all do this).
- **Database driver: synchronous Postgres** via `psycopg[binary]` + `sqlalchemy` (no `[asyncio]` extra, no `asyncpg`). FastAPI handlers use `def` not `async def` for DB-touching paths; FastAPI runs them in a threadpool. Simpler mental model, fewer event-loop pitfalls, and Celery workers are sync anyway.
- **No OpenTelemetry in MVP.** Structured JSON logs via structlog → CloudWatch is enough for the rubric. OTel is iteration-phase if at all.

---

## Request Model (universal fields on every provisioning request)

Every resource-provisioning request — regardless of resource type — carries a common header. These five fields are validated, persisted on `provisioning_requests`, and propagated as **AWS resource tags** on whatever Terraform creates. They also drive policy decisions (e.g. "prod requests in `us-east-1` only").

| Field | Type | Validation | Notes |
|---|---|---|---|
| `owner` | string | non-empty, ≤64 chars, matches `^[a-z][a-z0-9._-]{1,63}$` | Username/team identifier. Stored on the request and emitted as the `Owner` tag. |
| `cost_center` | string | matches `^CC-\d{4}$` (or whatever format the org uses — pick one and document) | Required for chargeback. Emitted as `CostCenter` tag. |
| `region` | enum | one of an allowlist (start with `us-east-1`, `us-west-2`) | AWS region. Drives Terraform provider config and AZ validation. |
| `availability_zone` | string | must belong to `region`; validated against a static `{region: [az,...]}` map seeded at app startup from a config constant | Some resource types (e.g. EBS, EFS mount targets) need an explicit AZ. For S3 it's ignored but still recorded. |
| `environment` | enum | `dev` \| `staging` \| `prod` | Drives policy (e.g. prod requires extra approver) and tagging. |

**Pydantic shape (one base + per-resource-type subclass):**

```
RequestHeader(BaseModel):           # owner, cost_center, region, availability_zone, environment
S3BucketSpec(BaseModel):            # bucket-specific: name, versioning, public_access_block (default true)
S3BucketRequest(RequestHeader):     # composes header + spec
    spec: S3BucketSpec
```

**Tag set applied to every provisioned resource** (enforced in the Jinja TF template, not trusted from the request):

```
Owner          = <header.owner>
CostCenter     = <header.cost_center>
Environment    = <header.environment>
ManagedBy      = "ngx-provisioner"
RequestId      = <request_id>           # UUID generated server-side
ProvisionedAt  = <ISO-8601 timestamp>
```

**`provisioning_requests` table columns (sync SQLAlchemy):**

```
id                UUID primary key (server-generated)
idempotency_key   text unique (client-supplied header)
resource_type     text not null         -- 's3_bucket', 'dynamodb_table', ...
status            text not null         -- 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'
owner             text not null
cost_center       text not null
region            text not null
availability_zone text not null
environment       text not null
spec              jsonb not null        -- the resource-type-specific payload
created_at        timestamptz default now()
updated_at        timestamptz default now()
celery_task_id    text                  -- correlation
error_message     text                  -- populated on failure
```

**`provisioned_resources` table columns:**

```
id                UUID primary key
request_id        UUID references provisioning_requests(id)
resource_type     text
arn               text
terraform_state_key text                 -- s3 key of the .tfstate
outputs           jsonb                  -- terraform output -json
created_at        timestamptz default now()
destroyed_at      timestamptz            -- null until DELETE'd
```

These two tables + an `audit_events` append-only table cover the audit trail. Indices: `(status, created_at)`, `(owner)`, `(idempotency_key UNIQUE)`.

---

## Execution Flow

**Three running processes**, all built from the same Docker image with different commands:

```
┌────────────────────┐    ┌────────────────────┐    ┌────────────────────┐
│  API container     │    │  Redis             │    │  Worker container  │
│  (FastAPI)         │    │  (Celery broker)   │    │  (Celery worker)   │
│  uvicorn ngx.main  │    │  redis-server      │    │  celery -A ngx     │
│  :app              │    │                    │    │  worker -c 2       │
└─────────┬──────────┘    └─────────┬──────────┘    └─────────┬──────────┘
          │                         │                         │
          └──────────── all three reach Postgres (Aurora) ────┘
```

The API and worker share Pydantic models, DB models, and Celery task signatures — same image, no schema drift. The Dockerfile installs the pinned `terraform` binary unconditionally; only the worker actually invokes it.

**End-to-end lifecycle of one request:**

1. **HTTP `POST /v1/buckets`** with `Idempotency-Key` header and JSON body (header fields + spec). Pydantic validates, including AZ-belongs-to-region. <100ms response.
2. **API handler** (`src/ngx/api/buckets.py`):
   - On `Idempotency-Key` collision → return existing row.
   - Else insert `provisioning_requests` row with `status='PENDING'`.
   - Call `provision_s3_bucket.delay(str(pr.id))` — pushes a JSON message to a Redis list, returns immediately.
   - Persist `celery_task_id` on the row, return `202 Accepted` with `request_id` and `status: PENDING`.
3. **Redis** holds the message (list-based queue).
4. **Worker** (`celery -A ngx.workers.app worker -c 2 -Q provisioning`) blocking-pops the queue, hands the message to one of its 2 task slots, which invokes the registered `provision_s3_bucket(request_id)` task.
5. **Task** (`src/ngx/workers/tasks.py`):
   - Loads the row, sets `status='RUNNING'`, commits.
   - Constructs `TerraformRunner(resource_type, request_id, workdir=Path(f"/work/{request_id}"), state_key=f"provisioned/s3_bucket/{request_id}.tfstate")`.
   - Calls `.run(spec, header)` and catches errors:
     - `TerraformTransientError` → re-raise (Celery `autoretry_for` retries with backoff)
     - `TerraformError` (definite) → set `status='FAILED'`, persist `error_message`, raise (no retry)
     - Success → insert `provisioned_resources` row with ARN + outputs, set `status='SUCCEEDED'`.
6. **`TerraformRunner.run()`** (`src/ngx/services/terraform_runner.py`):
   - Creates `/work/{request_id}/`, renders `templates/{resource_type}.tf.j2` → `main.tf`, writes `terraform.tfvars.json`.
   - `terraform -chdir=/work/{id} init -backend-config=...` (S3 bucket + DDB lock + per-request `key`).
   - `terraform -chdir=/work/{id} plan -json -out=plan.bin -input=false`
   - `terraform -chdir=/work/{id} apply -json -auto-approve -input=false plan.bin`
   - `terraform -chdir=/work/{id} output -json` → captured into Postgres.
   - **Every line of `-json` output is parsed and emitted as a structlog event** with `request_id` so CloudWatch Insights can filter the full lifecycle of one request.
   - Workdir is deleted on success; kept on failure for debugging.
7. **Client polls** `GET /v1/buckets/{id}` — pure DB read, no Celery result-backend lookup. Postgres is the source of truth for status; Celery is just transport.

**Two layers of isolation:**

| Layer | Where | Per-request key |
|---|---|---|
| Local working files (`.terraform/`, `plan.bin`, rendered TF) | Worker filesystem | `/work/{request_id}/` |
| Remote state (`.tfstate`) | S3 with DynamoDB lock | `provisioned/{resource_type}/{request_id}.tfstate` |

**Why source-of-truth = Postgres, not Celery's result backend:**

- If Redis loses a message, the row is still `PENDING` in Postgres — a Celery beat reaper task can find stale `PENDING` rows older than N minutes and re-enqueue.
- Idempotency at HTTP layer (`Idempotency-Key` unique) + idempotent `terraform apply` (per-request state means re-running is a no-op if resource matches) = a worker crash mid-apply followed by retry never produces duplicate buckets.

**Failure mode matrix:**

| Failure | Behavior |
|---|---|
| Pydantic validation fails | 422 returned; nothing in DB or Redis. |
| Idempotency-Key collision | 200 with existing row; no enqueue. |
| API persists then dies before enqueue | Reaper picks up stale `PENDING`, re-enqueues. |
| Worker dies mid-apply | Celery requeues; per-request state means re-apply is idempotent. |
| Terraform definite error (e.g. bucket name taken) | `status='FAILED'`, `error_message` populated, no retry. |
| Terraform transient error (rate limit) | Caught as `TerraformTransientError`, Celery retries with backoff. |

**Concurrency knobs:** worker container `-c 2` (two parallel slots). Per-request workdir + per-request state means parallel runs don't collide; the DDB lock only serializes if two tasks somehow target the same state key (which they shouldn't). Scale horizontally by raising the worker ECS service `desired_count`.

**Plugin caching:** Dockerfile sets `TF_PLUGIN_CACHE_DIR=/opt/tf-plugins` and pre-warms the AWS provider during build, so per-request `terraform init` doesn't re-download ~200MB each time.

---

**Hard requirements from PDF that MUST be true at submission:**
- [x] All workloads deployed via Terraform via CI/CD — `terraform.yml` (plan/apply w/ env gate) + `deploy.yml` (force-new-deployment)
- [ ] Reusable TF modules + `terraform test` coverage on at least one module — _flat `infrastructure/dev/main.tf`; no `modules/`, no `*.tftest.hcl`_
- [~] No wildcard IAM, no hardcoded secrets (SSM/Secrets Manager) — _deployer policy scoped to `forge-*` ARNs (PR #11); no SSM/Secrets Manager wired yet (nothing to store)_
- [x] Service is containerized, exposed via ALB, **reachable for demo** — Fargate task behind `forge-dev-alb`
- [ ] Service does input validation, error handling, structured logging — _only health endpoints exist; no Pydantic models, no structlog_
- [ ] Service writes to S3 / DynamoDB / Aurora — _no data layer yet_
- [ ] CloudWatch alarms with SNS notifications for key metrics (ECS CPU/mem) — _log group only_
- [~] README + DECISIONS.md (1–2 paragraphs on a key choice) — _README done; DECISIONS.md missing_
- [~] `diagrams/` with draw.io `.xml` + image — _`docs/diagrams/NGX_Networkinig.drawio` exists (filename typo); no PNG export_
- [x] `CLAUDE.md` present
- [ ] **At least one PR left open** showing AI iteration — _PR #2, #3, #11 all merged_

---

## Scoring-driven minimum bar (the "must have" matrix)

This is what each rubric category needs at minimum to score well. Anything beyond is iteration.

### Service (30%) — minimum bar

- [~] FastAPI app with: `POST /v1/buckets`, `GET /v1/buckets/{id}`, `GET /v1/buckets`, `GET /health/live`, `GET /health/ready`, `GET /docs` — _only `/livez`, `/readyz`, `/`, `/docs`, `/openapi.json` exist; bucket endpoints not implemented_
- [ ] Pydantic request validation (name regex, region allowlist, tags required: `Owner`, `CostCenter`)
- [ ] Idempotency on `POST` (client-supplied `Idempotency-Key` header → unique constraint)
- [ ] Errors: 400 validation, 409 conflict (already exists / dup idempotency key), 422 policy violation, 500 mapped to RFC 7807 problem+json
- [ ] Structured JSON logs (`structlog` or `python-json-logger`), one log line per request with request_id correlation
- [ ] Records every request in Postgres (sync SQLAlchemy + `psycopg[binary]`): `provisioning_requests`, `provisioned_resources`, `audit_events`
- [ ] **Execution: API enqueues to Celery (Redis broker); worker shells out to pinned `terraform` binary.** Per-request workdir, per-request remote state. `-json` output streamed into structlog. (Celery+Redis is **in MVP** because it's the execution path.)
- [ ] **Decision captured in DECISIONS.md**: "Why shell out to the Terraform binary in a Celery worker, not Terraform Cloud and not a Python wrapper library" — the wrapper landscape (`python-terraform`, `libterraform`, CDKTF) all still require the binary; `tfexec` is Go-only; binary + per-request state is the canonical pattern (Atlantis/Terragrunt model).

### Terraform (25%) — minimum bar

- [ ] Module-per-concern under `terraform/modules/`: `network`, `ecr`, `ecs_service`, `aurora_serverless`, `kms`, `secrets` — _everything flat in `infrastructure/dev/main.tf`_
- [~] Single env at `terraform/envs/dev/` composing the modules — _`infrastructure/dev/` exists but doesn't compose modules yet_
- [ ] **At least one `*.tftest.hcl`** — start with `kms` module (assertions: rotation enabled, key policy has no `Principal: "*"`, alias matches)
- [~] `terraform fmt` + `terraform validate` + `tflint` + `checkov` running in CI — _`fmt -check` + `validate` + `plan` in `terraform.yml`; `tflint` and `checkov` not added_
- [ ] OIDC trust between GitHub Actions and AWS (no static AWS keys in repo or GH secrets — only the role ARN) — _still using long-lived `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`_
- [x] Backend: S3 + DynamoDB lock. Bootstrap script (`terraform/bootstrap/`) creates the state bucket + lock table outside the main stack. Document the one-time bootstrap step in README. — `infrastructure/bootstrap/` + README walkthrough
- [~] Least-privilege IAM: ECS task role only allows what the service does (e.g., `s3:CreateBucket`, `s3:PutBucketTagging`, `s3:PutEncryptionConfiguration`, `s3:PutPublicAccessBlock` scoped to `arn:aws:s3:::ngx-managed-*`); task **execution** role separate from task role — _execution role exists with AWS-managed policy; no separate task role yet (service doesn't provision anything yet). Deployer IAM policy was scoped to `forge-*` in PR #11._
- [ ] All sensitive values via Secrets Manager (DB credentials) or SSM Parameter Store (config)
- [ ] **Option 1 inclusions in MVP**: KMS CMK on Aurora storage + CloudWatch log groups + S3 state bucket. (TLS on ALB, CMK on everything else, Aurora IAM auth, autoscaling — iteration.)

### AI workflow (20%) — minimum bar

- [x] `CLAUDE.md` present (✓) — extend with: project glossary, do/don't list, decision log pointer
- [x] Branch-per-issue, conventional commits, **co-author trailer on every commit** — `issue-1`, `fix/ecr-iam-perms`, `issue-4`; merge commits, not squash; co-author trailer on every commit
- [~] Issue → PR cadence visible. Target ~5 PRs total, each scoped to one issue, **non-squash merges** (PDF requires this) — _3 of ~5 PRs landed (PR #2, #3, #11), all merge-commits_
- [ ] **One PR left open** at submission showing iteration in progress (e.g., adding DynamoDB resource type, or wiring Bedrock pre-flight check). PR description includes a short "AI collaboration notes" section
- [ ] Optional but high-leverage: a short `docs/AI_WORKFLOW.md` capturing 3–4 specific examples (one course-correction, one win, one limitation)

### CI/CD (15%) — minimum bar

- [x] Existing workflows kept: `format-lint.yml`, `unit-tests.yml`, `ci.yml`, `build-container.yml` (✓)
- [~] **Add `terraform.yml`**: fmt → validate → tflint → checkov → `terraform plan` on PR (post plan summary as PR comment) → `terraform apply` on push to main with environment protection — _fmt/validate/plan/apply + PR comment + `production` env gate done; `tflint` and `checkov` not added_
- [x] **Add `deploy.yml`**: after container builds and pushes to ECR, run `aws ecs update-service --force-new-deployment` — incl. `aws ecs wait services-stable`
- [x] Update `build-container.yml` to push to **ECR in addition to GHCR** (or replace; ECR is what ECS pulls from) — pushes `:latest`, `:<version>`, `:<sha>` to ECR
- [ ] One CloudWatch alarm + SNS topic with email subscription for ECS CPU > 80% (this is the minimum for the rubric's "alarms with SNS notifications" line — not full observability)

### Docs (10%) — minimum bar

- [~] `README.md`: what it is, architecture diagram embedded, deploy steps (bootstrap → terraform apply → push container), API examples (curl), demo URL, local dev (docker-compose), teardown — _structure + deploy steps + local dev done; no live demo URL, no API examples (no API yet), no teardown section_
- [ ] `DECISIONS.md`: one key decision expanded — recommended: **"Why shell out to the Terraform binary in a Celery worker, not Terraform Cloud and not a Python wrapper library"**
- [~] `diagrams/architecture.drawio` + `architecture.png` — _`docs/diagrams/NGX_Networkinig.drawio` exists (filename typo `Networkinig`); no `.png` export_
- [ ] `NGX_CHALLENGE_DECISIONS.md` (referenced by CLAUDE.md) — running architecture log

---

## Phase plan (sequenced PRs)

Each PR is its own branch (`issue-N`), reviewed and merged with merge commit (no squash). Co-author trailer on every commit.

### MVP Phase — get to a demoable, deployed service

**PR 1 — `issue-1`: Application skeleton + worker + Dockerfile + local dev** (~one evening)
- `src/ngx/{__init__.py, main.py, config.py}`
- `src/ngx/api/{__init__.py, health.py, buckets.py}` — FastAPI routers, sync handlers (`def`, not `async def`)
- `src/ngx/schemas/{__init__.py, header.py, s3_bucket.py}` — Pydantic `RequestHeader`, `S3BucketSpec`, `S3BucketRequest`
- `src/ngx/db/{__init__.py, models.py, session.py}` — sync SQLAlchemy 2.0 + `psycopg[binary]`
- `src/ngx/services/{__init__.py, policy.py, terraform_runner.py}` — policy engine + the subprocess-wrapping Terraform runner
- `src/ngx/templates/s3_bucket.tf.j2` — Jinja template that renders to `main.tf` per request
- `src/ngx/workers/{__init__.py, app.py, tasks.py}` — Celery app + `provision_s3_bucket` / `destroy_s3_bucket` tasks
- `src/ngx/observability/logging.py` — structlog config, request_id middleware
- `tests/unit/{test_health.py, test_request_validation.py, test_policy.py, test_terraform_runner.py}` — `terraform_runner` test mocks `subprocess.run` and asserts the right CLI args + state key construction
- `Dockerfile` (multi-stage, non-root, slim Python 3.12, uv install, **pinned `terraform` binary copied from `hashicorp/terraform:1.9.8`**, `TF_PLUGIN_CACHE_DIR` set, AWS provider pre-warmed)
- `docker-compose.yml` for local Postgres + Redis + api + worker
- `alembic/` initialized, baseline migration creates `provisioning_requests`, `provisioned_resources`, `audit_events`
- All paths work locally with docker-compose; `pytest -m unit` green; ruff/mypy green
- `pyproject.toml` changes: **remove** `sqlalchemy[asyncio]`, `asyncpg`, `opentelemetry-*` (3 packages), **and `boto3`** (we don't use AWS SDKs — secrets are injected as env vars by ECS via the task definition's `secrets[]` block, which Terraform wires up to Secrets Manager at apply time). **Add** `psycopg[binary]`, `structlog`, `jinja2`, `testcontainers[postgres]`. Keep `celery[redis]`, `redis`. Drop `moto` from the test deps too — there's nothing to mock; tests mock `subprocess.run` for the Terraform runner.

**PR 2 — `issue-2`: Terraform foundations (network, ECR, KMS, secrets, Aurora, ECS)**
- `terraform/bootstrap/` — state bucket + lock table (run once manually; documented)
- `terraform/modules/network/` — VPC, 2 AZs, public + private subnets, **single NAT instance** (cost) with note about the HA tradeoff
- `terraform/modules/kms/` — CMK with rotation, alias, narrow key policy. **Includes `tests/kms.tftest.hcl`** with run blocks asserting rotation enabled, no wildcard principals.
- `terraform/modules/ecr/` — repo + lifecycle policy (keep last 10 images)
- `terraform/modules/aurora_serverless/` — Aurora Serverless v2 Postgres, min ACU 0.5, max ACU 1, KMS-encrypted, password in Secrets Manager, **no public access**
- `terraform/modules/ecs_service/` — cluster, task def, service, ALB (HTTP only in MVP, HTTPS in iteration), task role with narrow S3 perms, exec role separate
- `terraform/envs/dev/main.tf` — composes all of the above
- `terraform/envs/dev/variables.tf` + `terraform.tfvars.example`

**PR 3 — `issue-3`: Terraform CI workflow + GitHub OIDC + ECS deploy workflow**
- `.github/workflows/terraform.yml`: fmt, validate, tflint, checkov, plan-on-PR (with PR comment), apply-on-main (with environment protection)
- `.github/workflows/deploy.yml`: triggers after `build-container.yml` succeeds on main, runs `aws ecs update-service --force-new-deployment`
- Modify `build-container.yml` to push to ECR (additionally or instead of GHCR)
- `terraform/modules/github_oidc/` (or inline in env) — IAM role for GitHub OIDC with trust policy scoped to this repo
- README section: bootstrap → first deploy walkthrough
- **End of MVP phase: deploy succeeds, ALB URL returns 200 on `/health/ready`, can `curl POST /v1/buckets` and see the bucket created**

**PR 4 — `issue-4`: Minimum observability + alarms + DECISIONS.md + diagrams**
- `terraform/modules/observability/` — one CloudWatch alarm (ECS CPU > 80%), SNS topic, email subscription via variable
- Service: structlog JSON logging wired up, request_id middleware
- `DECISIONS.md` — "Terraform binary in a Celery worker vs alternatives" decision written up
- `diagrams/architecture.drawio` + exported PNG (system architecture — required by PDF)
- `docs/flows/REQUEST_EXECUTION.md` + `docs/flows/request_execution.drawio` + exported PNG — sequence diagram of one request from `POST /v1/buckets` through Celery, Redis, the Terraform runner, S3 state, and back to the client polling `GET /v1/buckets/{id}`. Include both happy path and a failure branch.
- `NGX_CHALLENGE_DECISIONS.md` started (running log)
- README polished: deploy steps verified end-to-end, demo URL, API examples, teardown command, links to both diagrams

### **MVP DONE** — service is live, deployable, documented, scored against every rubric line.

### Iteration Phase — Option 1 deepening + the open PR

**PR 5 — `issue-5`: TLS on ALB + Aurora IAM auth + autoscaling** (still merge)
- ACM cert (DNS-validated via Route53 if you own a domain; otherwise self-signed for demo with documented caveat)
- HTTPS listener, redirect HTTP → HTTPS
- Aurora IAM database auth: app uses IAM token instead of password from Secrets Manager (rotate / shrink Secrets Manager scope)
- ECS service autoscaling: target tracking on CPU 50% and ALB request count
- Add `tests/ecs_service.tftest.hcl` asserting task role has no wildcard actions

**PR 6 — `issue-6` (LEFT OPEN at submission)**: Add second resource type (DynamoDB) with Bedrock pre-flight policy checker
- Adds `POST /v1/dynamodb-tables`
- Adds a Bedrock-backed `services/policy_review.py` that takes a parsed request and returns risk findings before the resource is created (cheap model, structured JSON output)
- PR description includes "AI collaboration notes": one example of an AI course-correction, one example of an AI win, one example of where AI was wrong
- Multiple commits showing iteration; do not merge before submission

---

## Critical files (paths to be created/modified)

**Already exists (don't recreate):** `CLAUDE.md`, `pyproject.toml`, `.gitignore`, `.pre-commit-config.yaml`, `.github/workflows/{ci,format-lint,unit-tests,build-container}.yml`, `.claude/settings.json`

**To be created (MVP):**
- `Dockerfile`
- `docker-compose.yml`
- `src/ngx/main.py`
- `src/ngx/config.py`
- `src/ngx/api/health.py`
- `src/ngx/api/buckets.py`
- `src/ngx/schemas/header.py`
- `src/ngx/schemas/s3_bucket.py`
- `src/ngx/db/models.py`
- `src/ngx/db/session.py`
- `src/ngx/services/policy.py`
- `src/ngx/services/terraform_runner.py`
- `src/ngx/templates/s3_bucket.tf.j2`
- `src/ngx/workers/app.py`
- `src/ngx/workers/tasks.py`
- `src/ngx/observability/logging.py`
- `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_baseline.py`
- `tests/unit/test_health.py`
- `tests/unit/test_request_validation.py`
- `tests/unit/test_policy.py`
- `tests/unit/test_terraform_runner.py`
- `tests/conftest.py`
- `terraform/bootstrap/main.tf`
- `terraform/modules/network/{main,variables,outputs}.tf`
- `terraform/modules/kms/{main,variables,outputs}.tf` + `tests/kms.tftest.hcl`
- `terraform/modules/ecr/{main,variables,outputs}.tf`
- `terraform/modules/aurora_serverless/{main,variables,outputs}.tf`
- `terraform/modules/ecs_service/{main,variables,outputs}.tf`
- `terraform/modules/observability/{main,variables,outputs}.tf`
- `terraform/envs/dev/{main,variables,outputs,terraform.tfvars.example,backend.tf}.tf`
- `.github/workflows/terraform.yml`
- `.github/workflows/deploy.yml`
- `README.md` (currently empty)
- `DECISIONS.md`
- `NGX_CHALLENGE_DECISIONS.md`
- `diagrams/architecture.drawio` — system architecture (PDF requirement: `diagrams/` directory with `.xml`/`.drawio`)
- `diagrams/architecture.png`
- `docs/flows/REQUEST_EXECUTION.md` — narrative walkthrough of the request lifecycle, embedding the sequence diagram and linking back to `src/ngx/api/buckets.py`, `src/ngx/workers/tasks.py`, `src/ngx/services/terraform_runner.py`
- `docs/flows/request_execution.drawio` — sequence diagram (lifelines: Client, API, Postgres, Redis, Worker, Terraform, S3 state, AWS) covering happy path + a failure branch
- `docs/flows/request_execution.png` — exported render embedded in `REQUEST_EXECUTION.md` and linked from `README.md`

**To be modified (MVP):**
- `pyproject.toml` — remove `sqlalchemy[asyncio]`, `asyncpg`, `opentelemetry-*`; add `psycopg[binary]`, `structlog`, `jinja2`, `moto[s3]`, `testcontainers[postgres]`
- `.github/workflows/build-container.yml` — push to ECR (in addition to or instead of GHCR)

---

## Risks & mitigations

1. **Aurora cold-start on first request after idle** — set min ACU = 0.5 (not zero); document a warm-up curl in README; or fall back to RDS Postgres `db.t4g.micro` if budget bites.
2. **NAT cost** — use single NAT instance, not NAT gateway. Document the HA tradeoff in DECISIONS.md.
3. **OIDC setup is finicky** — do this in PR 3 with extra time; have a fallback IAM user with short-lived access keys ready (don't commit them).
4. **State bootstrap chicken-and-egg** — keep `terraform/bootstrap/` separate; document explicitly as a one-time manual step. Don't try to make this self-bootstrapping.
5. **Demo URL goes down between submission and interview** — provide a `make demo-up` / `make demo-down` pair; document estimated 10-min spin-up time; offer to spin up live during the interview if the URL is down to control cost.
6. **Container build pushing to GHCR vs ECR** — ECS pulls from ECR. Either replace GHCR with ECR or push to both. Decide in PR 3.

---

## Verification (end-of-MVP smoke test)

From a clean machine after merging PRs 1–4:

```bash
# Local
docker compose up -d
uv run pytest -m unit
uv run uvicorn ngx.main:app --reload
curl http://localhost:8000/health/ready

# Cloud (after bootstrap + first apply)
cd terraform/envs/dev && terraform apply
# Push container via GHA build-container.yml workflow_dispatch
# Wait for deploy.yml to roll the ECS service

ALB_URL=$(terraform output -raw alb_url)
curl -i $ALB_URL/health/ready                    # 200
curl -i $ALB_URL/docs                             # FastAPI Swagger
REQ_ID=$(curl -s -X POST $ALB_URL/v1/buckets \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $(uuidgen)" \
  -d '{
    "owner":"jon.phillips",
    "cost_center":"CC-1234",
    "region":"us-east-1",
    "availability_zone":"us-east-1a",
    "environment":"dev",
    "spec":{"name":"ngx-managed-demo-001","versioning":true}
  }' | jq -r .id)                                 # 202 Accepted, status=PENDING

# Poll until status flips to SUCCEEDED
while [ "$(curl -s $ALB_URL/v1/buckets/$REQ_ID | jq -r .status)" != "SUCCEEDED" ]; do sleep 2; done

aws s3 ls | grep ngx-managed-demo-001             # bucket exists, tagged Owner/CostCenter/Env
curl $ALB_URL/v1/buckets                          # request shows in DB
```

CI verification: open a PR with a TF change → terraform plan posted as PR comment, no checkov/tflint findings.

---

## Out of scope for MVP (do not build until iteration)

- OpenTelemetry / distributed tracing (structlog → CloudWatch is sufficient for the rubric)
- Async DB layer (sync `psycopg` + sync SQLAlchemy in MVP)
- CloudWatch dashboard (one alarm only)
- Multiple environments (dev only)
- Multi-AZ Aurora failover, Multi-NAT
- Bedrock integration (PR 6, open)
- DynamoDB resource type (PR 6, open)
- Full alarm coverage on memory, ALB 5xx, Aurora ACU
- IAM database auth (PR 5)
- TLS on ALB (PR 5)
- ECS autoscaling (PR 5)
- Integration test suite with testcontainers (add in iteration if time)
- Reaper / stale-PENDING sweep (mention in DECISIONS.md as a planned safety net; implement only if time)
