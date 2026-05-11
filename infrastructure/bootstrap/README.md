# Terraform State Backend Bootstrap

> ⚠️ **RUN ONCE, EVER. NOT IN CI.**
>
> This stack provisions the S3 bucket and DynamoDB table that every other
> Terraform stack in this repo uses as its remote state backend.

## What it creates

- **S3 bucket** `forge-tfstate-<aws-account-id>`
  - Versioned (recoverable state if a bad apply corrupts it)
  - AES-256 server-side encryption
  - All public access blocked
- **DynamoDB table** `forge-tfstate-lock`
  - PAY_PER_REQUEST billing (no idle cost)
  - `LockID` hash key (Terraform's required schema)
  - Point-in-time recovery enabled

## How to run

```sh
terraform -chdir=infrastructure/bootstrap init
terraform -chdir=infrastructure/bootstrap apply
```

Required AWS permissions are in `infrastructure/policies/ngx-deployer-policy.json` —
make sure the `ngx-deployer` IAM user has the latest version applied before
running.

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
