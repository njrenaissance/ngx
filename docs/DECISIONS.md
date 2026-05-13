# Architecture Decision Records

Project-level decisions worth preserving outside of commit history. Each entry
captures the **why** so future contributors can revisit a choice with context.

Format per ADR: Context → Decision → Why → Consequences → Alternatives. Keep
each ADR tight; long discussions belong in the originating PR.

---

## ADR-001 — Python, FastAPI, Celery, Postgres chosen for developer familiarity

**Date:** 2026-03-01
**Status:** Accepted
**Originating context:** project bootstrap

### Context

The challenge brief leaves the language/runtime open. Options on the table were
Python (FastAPI / Django), Go (chi or net/http + sqlc), and Node (Fastify or
Express). Async-task backends considered: Celery, RQ, Arq (Python); Asynq,
River (Go); BullMQ (Node). Data store options: Postgres, MySQL, DynamoDB.

### Decision

Build the service in **Python 3.12** with **FastAPI** + **SQLAlchemy** +
**Celery** + **Postgres** (Aurora Serverless v2 in cloud, vanilla Postgres in
docker-compose).

### Why

- **Developer familiarity is the load-bearing reason.** The challenge is
  scored on judgement, architecture, and the AI-collaboration workflow — not
  on demonstrating exotic technology. Picking a stack the author can move
  quickly in maximises iteration speed and reduces the chance that a minor
  language footgun consumes time better spent on infra/correctness work.
- FastAPI's Pydantic integration aligns cleanly with the catalog/config-schema
  validation work the service does anyway.
- Celery is the canonical async-task framework in Python; it has the largest
  install base, the deepest docs, and integrates with both Redis and SQL
  result backends out of the box.
- Postgres is the default well-understood relational store. Aurora Serverless
  v2 satisfies "managed Postgres on AWS" without paying for an always-on
  cluster during iteration.

### Consequences

- The stack pins downstream choices: Alembic for migrations (not Flyway or
  raw SQL), psycopg2/asyncpg for drivers, pytest for tests.
- Celery's SQLAlchemy result backend lets us reuse the Aurora cluster for
  task results — see ADR-002 and ADR-010.
- Container size is ~250 MB unoptimised. Worth flagging but acceptable for
  Fargate (cold start is 5–10 s, not 30+).

### Alternatives considered

- **Go.** Tempting for ECS Fargate (smaller images, faster cold starts) but
  unfamiliar territory for this author; the slower iteration would have
  consumed the budget needed for the infra/observability story.
- **Node + Fastify.** Comparable familiarity but the async-task story is
  weaker (BullMQ requires Redis, no SQL backend option, fewer reference
  architectures for Fargate worker patterns).

---

## ADR-002 — Celery result-backend tables managed by Celery, not Alembic

**Date:** 2026-05-12
**Status:** Accepted
**Originating issue:** [#54](https://github.com/njrenaissance/ngx/issues/54)

### Context

Celery's SQLAlchemy result backend stores task state in two tables —
`celery_taskmeta` and `celery_tasksetmeta` — created lazily in the configured
result-backend database on first use. Forge's result backend points at the
same Aurora cluster the app uses (`db+postgresql+psycopg2://...`, see
[`forge/workers/__init__.py`](../src/forge/workers/__init__.py)).

We considered adding an Alembic migration that pre-creates these tables.

### Decision

Leave the tables under Celery's control. **Do not** add an Alembic migration
for them.

### Why

- Celery's result-backend schema is internal to Celery and may change between
  releases. Pinning it into our migrations would couple us to a particular
  Celery version forever (fix-forward migrations cannot be rolled back; see
  CLAUDE.md § Database Migrations and ADR-004).
- The DDL Celery issues on first use is a one-time cost. Subsequent worker
  starts find the tables and skip the DDL path.
- Alembic's history stays focused on application-domain tables, which is
  what reviewers care about.

### Consequences

- The DB role used by the Celery worker must have `CREATE` privilege on the
  target schema (today: `public`). The dev environment satisfies this because
  both the api and the worker connect with the Aurora master user.
- Narrowing the worker to a least-privilege DB role (a separate hardening
  ticket) needs one of: (a) grant `CREATE` on the result-backend schema to
  the worker role, or (b) move the result backend to a dedicated schema
  owned by a DDL-capable role with `GRANT INSERT/UPDATE/SELECT` to the
  worker role.

### Alternatives considered

- **Alembic migration pre-creating the tables.** Rejected: couples our
  migrations to Celery's internal schema.
- **Redis as result backend.** Rejected: Redis result backends are ephemeral
  and lose state on broker restart; the provisioning workflow needs
  durability across worker rolls.

---

## ADR-003 — Astral UV for Python environment + dependency management

**Date:** 2026-03-02
**Status:** Accepted
**Originating context:** project bootstrap, codified in CLAUDE.md

### Context

Python package management has several mature options: pip + venv, pipenv,
poetry, hatch, pdm, uv. The project needed one that handles dependency
locking, virtual environments, scripts/entry points, and CI reproducibility.

### Decision

Use **uv** (from Astral) for all dependency and environment management.
Configuration lives in `pyproject.toml`; the lockfile is `uv.lock`.

### Why

- Single tool covers venv creation, dependency installation, locking, and
  script execution (`uv run pytest`, `uv run ruff check`).
- Significantly faster than pip + pip-tools or poetry. CI install time is
  ~5 s versus ~30 s for poetry on the same dependency set.
- Lockfile format is deterministic and reviewer-friendly.
- Pre-commit hooks invoke ruff and mypy via `uv run ...`, keeping versions
  pinned to the lockfile rather than whatever the developer's global
  Python sees.

### Consequences

- All contributors need uv installed locally. The README documents the
  install path.
- CI uses `astral-sh/setup-uv` action; switching to another runtime is a
  CI workflow edit, not a code change.

### Alternatives considered

- **poetry.** Mature and widely adopted, but ~5× slower on install and
  CI; the lockfile is also more conflict-prone in PR review.
- **pip + pip-tools.** Reliable but multi-tool; needs separate venv
  management and doesn't run scripts.

---

## ADR-004 — Alembic migrations are fix-forward only

**Date:** 2026-03-10
**Status:** Accepted
**Originating context:** codified in CLAUDE.md § Database Migrations

### Context

Alembic supports both forward (`upgrade`) and backward (`downgrade`)
migrations. Some teams write both; others treat downgrade as a fiction.

### Decision

**Never** write a downgrade migration. To fix a problem in a prior
migration, write a new forward migration that corrects it.

### Why

- A downgrade that runs against partially-applied state (e.g. after a
  partial backfill) often loses data — the inverse of `ALTER TABLE ADD
  COLUMN x` is not `DROP COLUMN x` if the column has been populated.
- Schema history with mixed up/down operations is harder to reason about
  than a strictly linear forward history.
- In practice, production rollbacks happen at the app layer (deploy the
  prior image) while the schema stays compatible across both versions
  for the rollback window. That discipline is incompatible with
  destructive downgrades.

### Consequences

- Migration authors must design forward changes to be backward-compatible
  with the previous app version for at least one release.
- "Oops" migrations that introduce bad changes are corrected by a new
  forward migration, not by `alembic downgrade`.
- The `downgrade()` function in each Alembic revision is left as
  `pass` (or `raise NotImplementedError("fix-forward only")`).

### Alternatives considered

- **Reversible migrations.** Standard Alembic practice. Rejected because
  the cost of writing correct downgrades exceeds the value, given the
  team's deployment model.

---

## ADR-005 — Aurora Serverless v2 for the dev database

**Date:** 2026-03-15
**Status:** Accepted
**Originating context:** infrastructure/modules/database

### Context

The challenge requires a managed AWS database. Options: RDS Postgres on a
fixed instance (e.g. `db.t4g.micro`), Aurora Postgres (fixed-instance
cluster), Aurora Serverless v2.

### Decision

Use **Aurora Serverless v2** (PostgreSQL flavour) with a single writer
instance in dev. ACU range 0.5–2.0 (minimum allowed by AWS, modest
ceiling for cost control).

### Why

- Serverless v2 scales to 0.5 ACU when idle, costing roughly the same as a
  `db.t4g.micro` while supporting demo-grade load if a reviewer pokes at
  it. Fixed-instance RDS doesn't scale down.
- The multi-AZ-ready subnet group is in place from day one — adding a
  reader instance later is a one-line change with no downtime.
- Aurora-specific features (point-in-time recovery, snapshot export to S3)
  satisfy the rubric's "demonstrate AWS competence" angle better than
  vanilla RDS.

### Consequences

- Cold-start latency on the very first connection after a full scale-down
  is a few hundred ms. The api uses connection pooling so subsequent
  requests are unaffected.
- The cluster endpoint (writer-only today) is exposed as
  `module.database.endpoint`. Adding a reader endpoint would be an output
  addition, not a refactor.
- Engine-neutral module surface: outputs are named `endpoint`,
  `database_name`, etc. — not `aurora_endpoint`. A future swap to RDS
  Postgres would be internal to the module.

### Alternatives considered

- **RDS Postgres (fixed instance).** Cheaper at full idle but doesn't
  scale to absorb load and lacks the "managed serverless" demo angle.
- **DynamoDB.** Not relational; would force re-modelling the catalog +
  provisioning tables in a way that loses join power.

---

## ADR-006 — GitHub OIDC for CI deploy authentication

**Date:** 2026-04-01
**Status:** Accepted
**Originating PR:** #19

### Context

CI needs AWS credentials to run `terraform plan`/`apply`, push to ECR,
update ECS services. Options: long-lived static IAM user keys stored in
GitHub Secrets, or short-lived federated credentials via GitHub's OIDC
provider.

The initial setup used a static `ngx-deployer` IAM user with secret-key
auth. PR #19 cut over to OIDC.

### Decision

Use **GitHub OIDC** to federate into an AWS IAM role
(`AWS_DEPLOY_ROLE_ARN`). No static IAM user keys in GitHub Secrets.

### Why

- Static keys are stolen credentials waiting to happen. Rotating them is
  manual; revoking them in incident response is a coordination problem.
- OIDC tokens are short-lived (1 hour by default), workflow-scoped, and
  bound to the specific repo + branch via the role's trust policy.
- The OIDC trust policy can require an environment (e.g. `production`)
  for apply, which lets us gate destructive operations behind GitHub
  Environment approval — see ADR-007.

### Consequences

- The OIDC provider and the deploy role are created **out of band** (the
  bootstrap stack is run once by a human), not by the dev composition.
  Terraform can't manage the role it itself runs as.
- All AWS-touching workflows use `aws-actions/configure-aws-credentials@v4`
  with `role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}`. There is no
  fallback to static keys.
- The KMS module's KeyAdministrators statement references this role
  explicitly — losing it would orphan the CMK.

### Alternatives considered

- **Static IAM user keys (original setup).** Rejected for the reasons above.
- **GitHub-hosted self-hosted runners with EC2 instance profiles.**
  Strictly more capable but adds operational overhead (runner lifecycle,
  patching, scaling) that this project doesn't need.

---

## ADR-007 — ECR `:latest` tag with `force-new-deployment` for image deploys

**Date:** 2026-04-05
**Status:** Accepted
**Originating context:** `.github/workflows/deploy.yml`

### Context

ECS Fargate pulls images from ECR by URI. Two common deploy models:
(a) tag each build with a unique tag (git SHA), update the task definition
to reference the new tag, register a new task-definition revision, point
the service at it; or (b) always push to `:latest`, force the service to
redeploy, ECS pulls the new image on task start.

### Decision

Push every CI build to ECR with the `:latest` tag. Trigger a rolling
redeploy with `aws ecs update-service --force-new-deployment`. The ECS
task definition references `:latest` permanently — no new revisions per
deploy.

### Why

- Single-tag deploys are operationally simpler: one mutable reference,
  no task-definition revisions piling up.
- `force-new-deployment` is atomic from the ECS service's perspective —
  it drains tasks one at a time with the configured `minimumHealthyPercent`
  / `maximumPercent` semantics.
- For a demo / single-environment project, the loss of "pin the
  deployed image to a specific SHA in Terraform state" is acceptable.
  Production-grade projects should pin by SHA.

### Consequences

- Rolling back means re-pushing the prior image to `:latest`, not pointing
  the task definition at a different tag. CI keeps prior images in ECR
  (lifecycle policy retains the last 10 untagged images), so this is
  possible but manual.
- Terraform `plan` doesn't diff on every CI build, because the task
  definition reference is stable. That's the whole point.

### Alternatives considered

- **Pin to SHA in task definition.** Stronger reproducibility but creates
  a Terraform diff per build, which forces every PR's CI to touch infra.
  Wrong shape for this project's scale.
- **Blue/green via CodeDeploy.** Overkill for a single ECS service with
  one task.

---

## ADR-008 — Customer-managed KMS key (CMK) over AWS-managed default

**Date:** 2026-03-20
**Status:** Accepted
**Originating context:** rubric requirement (Option-1) + `infrastructure/modules/kms`

### Context

AWS services can encrypt at rest with either the AWS-managed default key
(per service: `aws/rds`, `aws/elasticache`, `aws/secretsmanager`, etc.)
or a customer-managed key (CMK) in KMS. The challenge rubric explicitly
calls out customer-managed encryption keys as a requirement.

### Decision

Provision one **customer-managed CMK** per environment with alias
`alias/forge-<env>`. Use it for:

- Aurora cluster storage encryption
- Aurora master password (Secrets Manager) encryption
- ECS CloudWatch log group encryption
- Elasticache at-rest encryption (added by #54)

Enable annual rotation. Separate `KeyAdministrators` and `KeyUsers`
statements in the key policy — administrators provision/manage the key
but cannot decrypt, users can decrypt but cannot manage. The account root
gets a safety-net `kms:*` statement to avoid orphaning the key.

### Why

- Rotation, deletion windows, and policy edits are auditable per-key with
  a CMK. AWS-managed keys silently rotate and don't expose policy.
- A single shared CMK across services means key revocation has a clear
  blast radius (everything the project encrypts). Per-service AWS-managed
  keys would each need to be revoked separately during incident response.
- The separation of admin and user permissions is the actual reason CMKs
  exist: a compromised admin role can't read data, a compromised user
  role can't disable the key.

### Consequences

- Every service module that encrypts at rest takes `kms_key_arn` as an
  input variable. This is enforced by `terraform test` assertions in the
  kms module (`key_policy_grants_required_service_principals`) and the
  cache module.
- The OIDC deploy role is the `KeyAdministrators` principal. Day-to-day
  Terraform runs administer the key; reading encrypted data is delegated
  to AWS services via the user statement.

### Alternatives considered

- **AWS-managed default keys.** Rejected: doesn't satisfy the rubric and
  loses policy auditability.
- **Per-service CMK.** Stronger blast-radius isolation but multiplies
  policy maintenance. Not justified at this project's scale.

---

## ADR-009 — Celery broker pattern: Redis broker + Aurora SQL result backend

**Date:** 2026-05-08
**Status:** Accepted
**Originating PR:** #53 (E.1 wiring proof)

### Context

Celery decouples the broker (where pending tasks queue) from the result
backend (where finished task state is stored). Both are independently
configurable. Options:

- Broker: Redis, RabbitMQ, SQS, SQLAlchemy (database).
- Result backend: Redis, SQLAlchemy (database), DynamoDB, S3.

### Decision

Use **Redis** (AWS Elasticache in cloud, docker-compose redis locally)
as the broker, and the **SQLAlchemy result backend** pointing at Aurora
as the result store. The broker URL is `rediss://` in cloud (transit
encryption) and `redis://` locally.

### Why

- Redis broker latency is sub-millisecond; SQS introduces seconds-scale
  visibility-timeout semantics that are wrong for an interactive
  provisioning API.
- Reusing Aurora as the result backend means no extra infrastructure
  for durable task state — one database to back up, one set of credentials
  to rotate. See ADR-002 for table-management consequences.
- Persisting result state in a SQL store (rather than Redis with TTL)
  matches the user-facing polling pattern: `GET /v1/resources/{id}/status`
  reads from the same DB that holds the `ResourceRequest` row.

### Consequences

- The cloud deployment needs both Elasticache (broker) and Aurora
  (result backend). They are independently managed by separate Terraform
  modules.
- The broker URL is sensitive enough to keep out of Terraform state as
  a literal but non-secret enough to live in plain task-def env vars
  (no AUTH token; see ADR-011). The `rediss://` scheme is enforced by
  the cache module's `transit_encryption_enabled = true`.
- Celery's `task_acks_late = True` + `task_reject_on_worker_lost = True`
  configuration relies on the broker being a durable queue. Redis is
  durable enough for the POC; SQS would be stricter but introduces
  visibility-timeout race conditions in the provisioning task.

### Alternatives considered

- **SQS broker.** Stricter delivery guarantees but the visibility-timeout
  model fights the long-running `terraform apply` execution pattern that
  arrives in E.3.
- **Redis result backend.** Faster reads but loses durability across
  Elasticache restarts. Provisioning state must outlive a broker reboot.

---

## ADR-010 — Shared application security group owned by the network module

**Date:** 2026-05-12
**Status:** Accepted
**Originating issue:** [#54](https://github.com/njrenaissance/ngx/issues/54)

### Context

The original layout put `aws_security_group.app` inside the `ecs_service`
module. The database module consumed `module.ecs_service.app_security_group_id`
as its ingress source. This worked while ecs_service was the only producer
of an SG that downstream modules referenced.

Issue #54 introduced the cache module. Both cache and ecs_service now need
each other's outputs (cache wants the app SG as ingress source; ecs_service
wants the cache endpoint as a task env var), forming a module-level cycle.

### Decision

Move `aws_security_group.app` from `modules/ecs_service` into
`modules/network` and expose it via `module.network.app_security_group_id`.
All ECS task `network_configuration` blocks (api + worker) reference it via
an `app_security_group_id` variable.

### Why

- Network is the natural home for shared SGs: it already owns the VPC,
  subnets, route tables, and NAT gateway.
- Breaks the cache↔ecs_service cycle without introducing a new "shared
  resources" module that would only hold one SG.
- The "all forge tasks live in the same SG" pattern is fine for this
  project's blast-radius envelope — both api and worker are first-party
  code with identical egress needs and similar ingress-source patterns.

### Consequences

- The `network` module now depends on `module.alb.security_group_id` for
  its ingress-from-ALB rule. `module "alb"` is declared before
  `module "network"` in `dev/main.tf`.
- A `moved {}` block in `dev/moved.tf` maps both the original root-level
  address (`aws_security_group.app`) and the intermediate
  `module.ecs_service.aws_security_group.app` address to the new
  `module.network.aws_security_group.app` location, so existing dev state
  files reach the new address regardless of which point they were at.
- ecs_service module no longer exposes `app_security_group_id` as an
  output; consumers read it from `module.network` instead. database
  module's dev/main.tf invocation was updated accordingly.

### Alternatives considered

- **`modules/security_groups`.** More invasive than the network move
  for a single SG; defer until there are multiple cross-module SGs to
  share.
- **Inline SG in `dev/main.tf` at the top level.** Breaks the "modules
  own their resources" pattern used everywhere else.

---

## ADR-011 — Single-node Elasticache POC posture (no AUTH, no multi-AZ)

**Date:** 2026-05-12
**Status:** Accepted
**Originating issue:** [#54](https://github.com/njrenaissance/ngx/issues/54)

### Context

Elasticache for Redis supports several hardening dimensions: AUTH token
(password), transit encryption (TLS), at-rest encryption (KMS),
multi-AZ replication, automatic failover, and parameter-group tuning.

Issue #54 needed Elasticache to bridge #49 (local compose wiring) to #51
(real terraform apply in the worker). Time/scope was limited.

### Decision

Single-node replication group with:

- `transit_encryption_enabled = true` (URL is `rediss://`)
- `at_rest_encryption_enabled = true` (project CMK)
- `auth_token = null` (no password)
- `num_cache_clusters = 1`
- `automatic_failover_enabled = false`
- `multi_az_enabled = false`

Suppress `checkov:CKV_AWS_31` (AUTH-required) with an inline justification.

### Why

- Transit encryption + SG-scoped 6379 ingress already restricts who can
  reach the broker. AUTH adds a layer but also adds operational
  complexity: rotating the AUTH token requires coordinating an ECS task
  redeploy because the broker URL changes.
- Multi-AZ replication doubles the cost (active reader instance) for a
  POC that doesn't yet need 99.95% availability. The subnet group spans
  two AZs so adding a replica later is a `num_cache_clusters = 2` change.
- Documenting "this is the POC posture" explicitly here means a future
  reviewer doesn't have to guess whether the absence of AUTH is an
  oversight or a deliberate choice.

### Consequences

- The broker URL `rediss://<endpoint>:6379/0` is non-secret and lives in
  plain task-def env vars. Adding AUTH later means moving the URL into
  Secrets Manager and updating both api and worker task definitions to
  reference it via `secrets[]`.
- Production deployment should flip AUTH on and re-evaluate multi-AZ
  cost vs. SLA. The cache module's interface stays the same; only the
  variable values change.

### Alternatives considered

- **AUTH from day one.** Adds a Secrets Manager secret, URL templating,
  and rotation operational concerns. Defer to staging.
- **Multi-AZ replica.** Defer until SLA targets require it.

---

## ADR-012 — Worker IAM role split: shared execution role, separate task role

**Date:** 2026-05-12
**Status:** Accepted
**Originating issue:** [#54](https://github.com/njrenaissance/ngx/issues/54)

### Context

ECS task definitions accept two IAM roles:

- **Execution role** — assumed by ECS-the-orchestrator before the container
  starts. Pulls images, fetches secrets, writes logs.
- **Task role** — assumed by the running container itself. The only role
  the application code can use to call AWS APIs.

The api and worker tasks have identical execution-role needs (same image,
same DB secret, same log group) but divergent task-role futures: the
worker will eventually need S3 permissions to manage forge-managed
buckets (issue #51), the api will not.

### Decision

**Reuse** the execution role across api + worker. **Split** the task role:
api keeps `aws_iam_role.ecs_task` (no attached policies today), worker
gets a new `aws_iam_role.ecs_worker_task` (also no attached policies
today, but the policy attachment point for #51 lives here).

### Why

- Splitting the execution role would duplicate the DB-master-secret
  policy attachment across two roles for no security gain — both
  containers need the same secret to start.
- Splitting the task role now (before any policies are attached) avoids
  retrofitting later. When #51 adds S3 permissions to the worker, the
  api task role is unchanged — minimum blast radius.
- The IAM principle of least privilege says the application-facing role
  should only grant what the application needs. The api doesn't need S3;
  the worker does. Two roles, two policy surfaces.

### Consequences

- The ecs_service module owns two task roles and exposes both ARNs as
  outputs (`task_role_arn`, `worker_task_role_arn`).
- Future PRs attaching policies to either role do so in this module, not
  in dev/main.tf. Keeps the module surface stable.
- A reader scanning the codebase for "what can the worker do" can grep
  for `ecs_worker_task` and find every attachment point.

### Alternatives considered

- **Single shared task role.** Simpler today but forces #51 to grant
  S3 permissions to both the api and the worker. Wrong shape.
- **Split execution role too.** No security gain (both containers need
  the same prestart capabilities) and doubles the maintenance surface.

---

## ADR-013 — Single-region deployment (`us-east-1` only)

**Date:** 2026-05-12
**Status:** Accepted
**Originating context:** project bootstrap; single cloud environment

### Context

A production-grade deployment might span multiple AWS regions for
disaster recovery — a global Aurora cluster with per-region writers, a
Route53 failover record, multi-region KMS key (MRK), and Secrets Manager
secret replication. The challenge window is a two-week demo with no
real availability SLA.

### Decision

Deploy to **`us-east-1` only**. No cross-region snapshot copy, no
cross-region read replicas, no Route53 failover routing.

### Why

- The cost of multi-region roughly doubles the idle bill per added
  region (~$90–100/mo for two regions vs. ~$45–50/mo for one).
- The RTO/RPO target for the demo is "if `us-east-1` blips, the demo
  is temporarily unavailable" — acceptable for a 2-week interview window.
- Documenting the path to multi-region is the deliverable; building
  it is not.

### Consequences

- The infrastructure is confined to `us-east-1`. The `var.aws_region`
  default in `infrastructure/dev/variables.tf` is hardcoded to
  `us-east-1`.
- To go multi-region for DR: replace `aws_rds_cluster` with
  `aws_rds_global_cluster` + per-region cluster instances; provision
  duplicate VPC / subnets / NAT / SG stacks via provider aliases;
  replicate the KMS CMK as a multi-region key; enable Secrets Manager
  multi-region replication; front the writer endpoint with a Route53
  failover record + health checks. Estimated cost: ~2× per added region.

### Alternatives considered

- **Multi-region active-active.** Complexity and cost not justified for
  a demo with a single developer and no real traffic.
- **Multi-region passive DR (warm standby).** Better RTO but still
  doubles cost for a demo window. Defer to a real production hardening
  pass.

---

## ADR-014 — Single-AZ Aurora writer (POC posture)

**Date:** 2026-05-12
**Status:** Accepted
**Originating context:** `infrastructure/modules/database`

### Context

Aurora Serverless v2 supports Multi-AZ with automatic failover: a writer
instance in one AZ and a reader in a second, with ~30 s automatic failover
on writer failure. Adding a reader roughly doubles the Aurora cost
(~$45/mo extra at the 0.5 ACU minimum).

### Decision

Run a **single writer instance** in one AZ. No reader instance. No
automatic AZ failover.

### Why

- Cost roughly doubles for a code-challenge demo that has no real
  availability target. The RPO/RTO for the demo is "if `us-east-1a`
  blips, the demo dies" — acceptable for a 2-week interview window.
- The Aurora DB subnet group already spans two AZs, so enabling Multi-AZ
  later is a one-variable change (`multi_az = true` on the cluster +
  one new `aws_rds_cluster_instance` resource block). No infrastructure
  redesign needed.

### Consequences

- A writer failure in `us-east-1a` requires manual intervention (promote
  a replica — but there is none) or a cluster restore from snapshot.
- Engine-neutral module surface: adding a reader endpoint is an output
  addition to the database module, not a refactor.

### Alternatives considered

- **Multi-AZ from day one.** Adds ~$45/mo for a demo window. Defer.
- **Aurora Global Database.** Cross-region writer promotion, ~1 min RTO.
  Overkill for a single-region POC.

---

## ADR-015 — Single AWS environment named `dev`

**Date:** 2026-05-12
**Status:** Accepted
**Originating context:** `infrastructure/dev/` directory naming

### Context

The `infrastructure/dev/` directory is the only Terraform root module that
CI manages. There is no separate staging or production stack. However, the
name `dev` implies it is not the live service — which it is.

### Decision

Keep the `dev` directory name. Treat it as **the live cloud environment
that backs the demo** for the duration of the challenge.

### Why

- PRs and issues already reference `infrastructure/dev/` paths. Renaming
  to `infrastructure/aws/` or `infrastructure/cloud/` would churn those
  references for no functional change.
- The production-safety knobs that would differ between dev and prod
  (`deletion_protection`, `skip_final_snapshot`, backup retention,
  Multi-AZ) are controlled by a single `var.production_safety` boolean.
  Defaults are `false` during iteration so the stack can be torn down and
  rebuilt cheaply; flip to `true` via tfvars before the demo
  (tracked by issue [#20](https://github.com/njrenaissance/ngx/issues/20)).
- Reviewers should read `dev` as "the only cloud environment" and not
  infer a missing staging tier.

### Consequences

- The CI pipeline targets `infrastructure/dev/` exclusively. A second
  environment would require a new root module and a parallel workflow.
- `var.production_safety` gates deletion protection and backup retention.
  Check its value in `terraform.tfvars` before a demo or audit.

### Alternatives considered

- **Rename to `infrastructure/aws/`.** Cleaner semantics but noisy
  rename diff with no functional benefit. Defer to when a real second
  environment lands.
- **Create a separate `infrastructure/prod/`.** Correct long-term shape
  but doubles the Terraform maintenance surface for a single-developer
  challenge with one real environment.

---

## ADR-016 — Split secret-vs-env-var credential injection for ECS tasks

**Date:** 2026-05-12
**Status:** Accepted
**Originating context:** `infrastructure/modules/ecs_service` + `modules/database`

### Context

ECS Fargate task definitions support two ways to pass configuration to
containers: `environment[]` (plain text, visible in task-definition
describe output) and `secrets[]` (fetched from Secrets Manager or SSM
Parameter Store at task launch, decrypted by the execution role, injected
as env vars without ever appearing in plaintext in the task definition).

The Aurora master credentials are stored in Secrets Manager as a JSON
object `{username, password}`. All other database connection parameters
(host, port, database name, username, SSL mode) are non-sensitive.

### Decision

Inject the Aurora master **password only** via `secrets[]`. Inject
everything else — host, port, database name, username, SSL mode, and all
Celery/app config — as plain `environment[]` entries.

### Why

- **Rate-limit economics.** Secrets Manager has a per-second API rate
  limit. Fetching every connection parameter as a secret would multiply
  the number of `GetSecretValue` calls at task launch proportionally.
  Only the password is actually sensitive.
- **Operator visibility.** `aws ecs describe-task-definition` reveals
  plain env vars, letting operators confirm connection config without
  needing `secretsmanager:GetSecretValue` permission. Useful in incident
  response.
- **Trust boundary.** The task role (assumed by the running container)
  has zero IAM permissions in the current implementation. Even if the
  container is compromised, the attacker can read `$FORGE_DATABASE__PASSWORD`
  from the process environment, but cannot call any AWS API to exfiltrate
  further data. Future service-level grants (S3 for managed-resources
  buckets — issue [#51](https://github.com/njrenaissance/ngx/issues/51))
  attach to the task role, keeping the execution role narrowly scoped to
  "start the task" forever.

### Consequences

- The execution role needs `secretsmanager:GetSecretValue` on the DB
  master secret ARN and `kms:Decrypt` on the CMK. Both are granted by a
  resource-scoped inline policy on the execution role
  (`ecs_execution_db_secret` in `modules/ecs_service/main.tf`).
- Any new credential added to the task (e.g. the Celery AUTH token if
  issue #51 adds ElastiCache AUTH) must decide: is it secret enough to
  warrant `secrets[]` injection? If yes, the execution role needs a new
  `GetSecretValue` grant and the task definition gets a new `secrets[]`
  entry. If no, add it to `environment[]`.

### Alternatives considered

- **All credentials via `secrets[]`.** Stronger defence-in-depth but
  multiplies Secrets Manager API calls and kills operator visibility.
  Not justified when the non-password fields are genuinely non-sensitive.
- **AWS Systems Manager Parameter Store instead of Secrets Manager.**
  Cheaper per-call pricing, but the Aurora master secret is already in
  Secrets Manager (provisioned by the database module). Using two
  credential stores for one task adds operational complexity.

---

## ADR-017 — S3-native locking for the managed-resources backend (no DynamoDB lock table)

**Date:** 2026-05-12
**Status:** Accepted
**Originating context:** `infrastructure/bootstrap/main.tf` + `src/forge/workers/workspace.py` (issue #51 / E.3)

### Context

The provisioning worker writes per-request Terraform state to a separate
S3 bucket (`forge-managed-resources-<account>`) — distinct from the
platform `forge-tfstate-<account>` bucket the rest of the repo uses.
The platform bucket has a DynamoDB lock table (`forge-tfstate-lock`)
because two operators running `terraform apply` concurrently against
the same stack would race; the lock serializes them.

Terraform 1.10+ supports S3-native state locking via the `use_lockfile`
backend argument: a sibling `<key>.tflock` object in the same bucket
acts as the lock. No second AWS service required.

### Decision

The managed-resources backend uses `use_lockfile = true` (configured
per-workspace by the worker in the rendered `backend.tf`). No DynamoDB
lock table is created for this bucket.

### Why

- **Cardinality of writers per state file is one.** Each per-request
  workspace has a unique tf_state_key (`{env}/{team_id}/standalone/
  {rr_id}/{logical_region}/terraform.tfstate`) and is touched by exactly
  one Celery task at a time. The contention DynamoDB solves — multiple
  operators racing against the same state — doesn't exist here. The
  one race that *can* happen (acks_late redelivery resuming a crashed
  task while a replacement task picks the same row) is exactly what
  lockfile semantics handle: the second worker waits, then proceeds
  with init/plan/apply against the now-current state.
- **Operational simplicity.** One AWS resource per backend (the bucket)
  instead of two. No PITR-billed DynamoDB table sitting idle 99% of
  the time. The lock object lives in the same bucket lifecycle and is
  bounded by the same noncurrent-version expiry.
- **Cost.** Negligible per-workspace storage + a single PUT/DELETE per
  apply for the lock object. DynamoDB on-demand pricing has a higher
  per-request floor than S3 even at low volume.

### Consequences

- Requires Terraform 1.10.0+ everywhere — pinned in the Dockerfile
  (`TERRAFORM_VERSION=1.10.0`) and enforced by checksum at build time.
  An older client trying to use this backend would silently skip
  locking; the version pin makes that impossible.
- Different code path from the platform backend (which still uses
  DynamoDB). A reader has to know which backend a given stack uses —
  flagged in [infrastructure/bootstrap/README.md](../infrastructure/bootstrap/README.md)
  and the per-workspace `backend.tf` template comment.

### Alternatives considered

- **Reuse the platform DynamoDB lock table.** Would put both backends
  on identical tooling but requires the worker IAM role to have
  DynamoDB write permissions — wider blast radius for a compromised
  worker than the S3-only grant we settled on. Worth nothing because
  the contention pattern doesn't justify DynamoDB at all.
- **Per-workspace DynamoDB tables.** Astronomical resource sprawl —
  one table per request — for zero contention benefit.
- **No locking at all (terraform's pre-1.10 default of `force_unlock`).**
  Doesn't survive the acks_late redelivery race; one of the racing
  tasks will corrupt state. Non-starter even for POC.
