# Terraform State Backend Bootstrap

> ⚠️ **RUN ONCE, EVER. NOT IN CI.**
>
> This stack provisions the S3 bucket and DynamoDB table that every other
> Terraform stack in this repo uses as its remote state backend, plus the
> separate managed-resources bucket + CMK that the provisioning *worker*
> writes to at runtime.

## What it creates

### Platform state backend (operator-managed)

- **S3 bucket** `forge-tfstate-<aws-account-id>`
  - Versioned (recoverable state if a bad apply corrupts it)
  - AES-256 server-side encryption
  - All public access blocked
- **DynamoDB table** `forge-tfstate-lock`
  - PAY_PER_REQUEST billing (no idle cost)
  - `LockID` hash key (Terraform's required schema)
  - Point-in-time recovery enabled

### Managed-resources backend (worker-writable; issue #51 / E.3)

- **CMK** alias `alias/forge-shared-managed-resources` — encrypts the bucket
  and is referenced by `packages/managed_database/v1/terraform/aws/main.tf`.
- **S3 bucket** `forge-managed-resources-<aws-account-id>`
  - Versioning + all-public-access-block + SSE-KMS with the CMK above
  - Bucket policy denies non-TLS access (`aws:SecureTransport=false`)
  - Lifecycle: noncurrent versions expire after 90 days; multipart aborts
    after 7 days
  - Locking: TF 1.10+ S3-native `use_lockfile = true` (configured per
    workspace in the worker-rendered `backend.tf`); **no DynamoDB lock
    table** — each per-request workspace has exactly one writer

This bucket is intentionally separate from the platform state bucket so
the worker's ECS task role can write here without ever being granted
access to the foundation backend.

## How to run

```sh
terraform -chdir=infrastructure/bootstrap init
terraform -chdir=infrastructure/bootstrap apply
```

Required AWS permissions are in `infrastructure/policies/ngx-deployer-policy.json` —
make sure the `ngx-deployer` IAM user has the latest version applied before
running.

> **⚠ Manual policy re-attach required for issue #51 / E.3.**
> The managed-resources bucket needs lifecycle-rule perms that weren't in
> the pre-#51 policy. After pulling this branch, an IAM admin must
> re-attach `infrastructure/policies/ngx-deployer-data-policy.json` to
> the `ngx-deployer` user (and the OIDC deploy role used by CI). The
> new actions are `s3:PutLifecycleConfiguration` and
> `s3:GetLifecycleConfiguration`, scoped to `arn:aws:s3:::forge-*`.

The bootstrap itself uses **local state** (no `backend "s3"` block). It can't
yet depend on the backend it's about to create.

## After it succeeds

- Note the outputs (`state_bucket_name`, `state_lock_table_name`,
  `aws_account_id`) — downstream stacks reference these in their `backend.tf`.
- The generated `terraform.tfstate` in this directory is `.gitignore`d. Save
  an encrypted backup somewhere outside the repo, or accept that
  `terraform import` can rebuild it if needed.
- Do **not** re-run unless you know what you're doing. Re-applying is
  idempotent in the happy path but every run adds risk.

## Why is this not in CI?

The state backend is the foundation everything else stands on. It should:

1. Be created by a human, deliberately, with awareness of what's happening.
2. Not be in the blast radius of an automated workflow that could
   accidentally `destroy` it (which would orphan all downstream stacks'
   state files).

CI scope is **`infrastructure/dev/`** and beyond. The
`.github/workflows/terraform.yml` workflow added in Phase D explicitly
targets `infrastructure/dev/` only.
