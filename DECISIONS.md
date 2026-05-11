# DECISIONS.md

A running log of architectural choices made during the build, with the
rationale behind each. The intent is twofold: keep the team aligned on
why-not-just-what for non-obvious calls, and let an outside reviewer trace
the platform-engineering posture without rebuilding the conversation.

---

## Why Aurora Serverless v2 (not RDS Postgres / DynamoDB / SQLite)

The challenge spec offers S3, DynamoDB, and Aurora as data-layer options.
We picked Aurora because it's part of the explicit "Option 1 — Complex
Terraform" track (KMS CMK, Aurora Serverless v2, autoscaling, IaC checks
in CI) we opted into. The choice is rubric-driven, not engineering-driven —
a defensible step-down would be RDS Postgres `db.t4g.micro` (~70% cheaper,
same Postgres semantics, same code path). We kept Aurora to stay aligned
with the locked path.

The `database/` module is **engine-neutral on purpose**. Its outputs
(`endpoint`, `database_name`, `master_secret_arn`) carry no Aurora-specific
naming. A future swap to RDS Postgres requires only changing the module's
internal implementation; the dev composition's `module "database"` block
does not change.

**Trade-offs we accepted:**

- **Cost:** ~$45/mo idle floor (0.5 ACU minimum, 24/7). RDS `db.t4g.micro`
  would be ~$13/mo.
- **Cold-start tax:** Serverless v2 keeps the 0.5 ACU warm so cold-start
  isn't actually a thing here, unlike Serverless v1 which paused.
- **Engine-version lag:** Aurora supports up to ~Postgres 16.4 as of Jan
  2026; we're a few months behind upstream Postgres. Fine for our usage.

---

## Single-region posture (`us-east-1`)

Production runs in `us-east-1` only. No cross-region snapshot copy, no
cross-region read replicas, no Route53 failover.

**To go multi-region for DR we would:**

1. Replace `aws_rds_cluster` with `aws_rds_global_cluster` + per-region
   `aws_rds_cluster` instances
2. Provision a duplicate VPC / subnets / NAT / app SG stack per region via
   provider aliases
3. Replicate the KMS CMK as a multi-region key (MRK)
4. Enable Secrets Manager multi-region secret replication
5. Front the writer endpoint with a Route53 failover record + health checks

**Estimated cost:** roughly 2× per added region (~$90–100/mo idle for two
regions) for ~1 minute RTO on regional outage. Not justified at this scale —
documenting the path is the deliverable, building it isn't.

---

## Single-AZ writer (within `us-east-1`)

The Aurora cluster runs a single writer instance in one AZ. No reader
instance, no automatic AZ failover.

**To enable Multi-AZ we would** flip `multi_az = true` on the cluster and
add a second `aws_rds_cluster_instance` resource for the reader (~30s
automatic failover, ~$45/mo extra).

**Why not now:** the cost roughly doubles the data layer for a code-challenge
demo that has no real availability target. RPO/RTO for the demo is
"if `us-east-1a` blips, the demo dies" — acceptable for a 2-week interview
window. The DB subnet group spans two AZs already, so flipping Multi-AZ on
later is one variable + one resource block.

---

## Single AWS environment (the `infrastructure/dev/` directory naming)

The directory is named `dev`, but it is the **only** cloud environment we
deploy. There is no separate staging or prod stack. Reviewers should read
`dev` as "the cloud environment that backs the live demo."

**Why we kept the name:** PRs and issues already reference `infrastructure/dev/`
paths; renaming to `infrastructure/aws/` (or similar) would churn those
references for no functional change. Tracked as a possible follow-up for
when a real second environment lands.

**What this means for posture:** the production-safety knobs that would
normally differ between dev and prod (`deletion_protection`,
`skip_final_snapshot`, backup retention, Multi-AZ) are configured on a
single `var.production_safety` boolean. Defaults are off during iteration
so we can tear down and rebuild cheaply; flipped on via tfvars before the
demo (tracked by issue #20).

---

## KMS Customer-Managed Key with explicit Key Administrators

The Aurora cluster, the DB master secret, and the ECS task's CloudWatch
log group are all encrypted at rest with a single customer-managed KMS key
(`alias/forge-dev`), not the AWS-managed defaults (`aws/rds`,
`aws/secretsmanager`).

**Why a CMK:** Option 1 of the challenge spec calls for it. Beyond the
rubric, a CMK gives us explicit principal control, full CloudTrail with
caller identity, and key rotation owned by us.

**Key policy structure** (separation of duties):

1. `EnableRootAccountAccess` — AWS-recommended safety net. This statement
   does NOT mean the AWS root user runs Terraform; it means IAM policies
   in this account can grant access to the key. Without it, the key can
   be permanently orphaned if explicit statements get misconfigured.
2. `KeyAdministrators` — explicit grant to the OIDC deploy role. KMS
   management actions (Create, Describe, Enable, List, Put, Update, Tag,
   Schedule deletion). **Explicitly excludes Encrypt/Decrypt** —
   administrators can manage the key but cannot read encrypted data.
   Day-to-day Terraform operations flow through this statement, not the
   safety-net root statement.
3. `AllowRDSEncryptDecrypt` / `AllowSecretsManagerEncryptDecrypt` /
   `AllowCloudWatchLogsEncryptDecrypt` — service principals as Key Users.
   Encrypt/Decrypt only.

**What this prevents:** if the OIDC deploy role is compromised, the
attacker can rotate, delete, or repolicy the key — but they cannot decrypt
data. If the running container is compromised (it has the task role, not
the deploy role), the attacker has zero KMS access at all.

**Tftest guards:** rotation enabled, no `Principal: "*"`, root statement
present, KeyAdministrators statement present with no Encrypt/Decrypt
actions, all three service principals present.

---

## Credential injection: split secret-vs-env-var pattern for ECS

The Aurora master credentials live in Secrets Manager as JSON
`{username, password}`. The ECS task gets the password via
`secrets[]`-style injection (the execution role calls
`secretsmanager:GetSecretValue` + `kms:Decrypt` at task launch). Everything
else (host, port, database name, username, SSL mode) is set as plain
`environment[]` env vars on the task definition.

**Why split:**

- Saves a Secrets Manager API call per non-password field at task start
  (Secrets Manager has a per-second rate limit)
- Operators can `aws ecs describe-task-definition` to see the connection
  config without needing `secretsmanager:GetSecretValue` permission
- Only the password is actually sensitive

**Trust boundary:** the task role (the role the running container assumes)
has zero IAM permissions in this PR. Even if the container is popped, the
attacker can read `$DATABASE_PASSWORD` from the environment but cannot
call any AWS API. Future service-level grants
(`s3:CreateBucket` on `forge-managed-*`, etc.) attach to the task role,
not the execution role — keeping the execution role narrowly scoped to
"start tasks" forever.

---

## OIDC deploy role + GitHub Actions

GitHub Actions workflows authenticate to AWS via OIDC, assuming the IAM
role `github-actions-ngx`. No long-lived AWS access keys exist in any
GitHub secret.

**Created out-of-band** in AWS (not Terraform-managed) because it's the
trust root for CI auth — Terraform can't bootstrap the role it itself runs
as. Documented in PR #19.

**Policies attached to the role** mirror two files in the repo, split along
the platform-vs-data axis so each domain can be reviewed independently:

- `infrastructure/policies/ngx-deployer-platform-policy.json` — VPC, ALB,
  ECS, IAM (role management scoped to `forge-*`), CloudWatch Logs, ECR,
  service-linked roles for ELB/ECS
- `infrastructure/policies/ngx-deployer-data-policy.json` — S3, DynamoDB,
  KMS, Secrets Manager, RDS, service-linked role for RDS

**Why two files instead of one:** AWS managed policies cap at 6,144
non-whitespace characters. A single combined policy hit that limit when
the data-layer additions (KMS + Secrets + RDS) were added in this PR.
Splitting along domain lines also enables independent review of the
data-layer permissions and gives us room to grow each domain separately.

**Why platform/data and not read/write:** the read/write split would enable
a read-only audit role pattern, but we have no audit-role use case in the
demo window. The platform/data axis maps to "domain ownership" — easier to
answer "what permissions does the data layer need" by opening one file.
A read/write split (or a 2x2 read × write × platform × data split) is a
recognized future-proofing direction if multiple personas (auditors,
data-scientists, app-developers) ever consume these policies. For now we
have one consumer (the CI deploy role) and YAGNI applies.

**Updates to either file require a manual policy-version update on the
attached entities** (the OIDC role and the legacy `ngx-deployer` IAM user)
in AWS before merge. The PR description for any change that adds new IAM
actions calls this out as a pre-merge step.
