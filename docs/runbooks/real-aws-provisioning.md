# Runbook — Real-AWS provisioning (manual, opt-in)

> **Not exercised by CI.** The integration test suite uses the
> deterministic fake terraform script (`tests/_fake_terraform/`) to
> exercise the plan-then-apply lifecycle without an AWS account. Real
> AWS runs are operator-driven.

This runbook drives a single `POST /v1/resources` through to a real AWS
resource via the Forge worker + the pinned `terraform` CLI.

## Prerequisites (one-time)

1. **Bootstrap stack applied** —
   `terraform -chdir=infrastructure/bootstrap apply` has been run by an
   operator. Required outputs: `managed_resources_bucket_name`,
   `managed_resources_bucket_region`, `managed_resources_kms_key_arn`.
2. **Updated deployer policy attached** — issue #51 added two new S3
   actions (`s3:PutLifecycleConfiguration`, `s3:GetLifecycleConfiguration`)
   to `infrastructure/policies/ngx-deployer-data-policy.json`. An IAM
   admin must re-attach the policy JSON to:
     - The `ngx-deployer` IAM user (used for manual workstation runs).
     - The OIDC deploy role (`github-actions-ngx`) used by CI.
   Without this, the bootstrap apply will fail with `AccessDenied` on
   the bucket lifecycle resource.
3. **Dev stack applied with the bootstrap outputs wired in** — set
   `managed_resources_bucket / _region / _kms_key_arn` in
   `infrastructure/dev/terraform.tfvars` (or `TF_VAR_*` env vars in CI),
   then `terraform -chdir=infrastructure/dev apply`. The worker ECS task
   role gets the scoped S3 + KMS grants automatically; no console work.
4. **Container image deployed** with `TERRAFORM_VERSION=1.10.0` baked
   in (the existing Dockerfile pins this — verified by SHA256 at build
   time).

## Driving a single real apply

1. **Override the fake binary on the worker.** The compose worker
   defaults to the fake script. For a real run set:
   ```sh
   export FORGE_TERRAFORM__BINARY=terraform
   ```
   In ECS, the production task definition does *not* set
   `FORGE_TERRAFORM__BINARY`, so the runner uses the default `terraform`
   on `$PATH` (the pinned 1.10.0 binary baked into the image). Nothing
   to override there.

2. **Provide AWS credentials to the worker.** Locally via `~/.aws/`
   mounted into the worker container, or `AWS_ACCESS_KEY_ID` /
   `AWS_SECRET_ACCESS_KEY` env vars. In ECS the task role is the
   credential — already scoped to S3 + KMS on the managed-resources
   bucket and CMK only (see `infrastructure/modules/ecs_service/main.tf`,
   `aws_iam_role_policy "ecs_worker_managed_resources"`).

3. **POST a request:**
   ```sh
   curl -X POST -H "Authorization: Bearer $FORGE_API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"resource_type":"managed_database","tier":"dev",
             "logical_region":"ngx-region-1a","name":"my-real-db",
             "config":{"engine":"postgres","size":"small","storage_gb":100}}' \
        https://<forge-host>/v1/resources
   ```

4. **Watch progress:**
   ```sh
   curl -H "Authorization: Bearer $FORGE_API_KEY" \
        https://<forge-host>/v1/resources/{id}/status
   ```
   Expected sequence: `pending → provisioning → provisioned`.
   `applying` and `planned` are intermediate `Deployment.status` values
   not surfaced through `/status` today (see "Known limitations" below).

5. **Inspect the audit log:**
   ```sh
   # Sanitized by SPEC App. B rule 1 — no ARNs/account IDs/regions.
   curl -H "Authorization: Bearer $FORGE_API_KEY" \
        https://<forge-host>/v1/resources/{id}/logs
   ```

## Tearing it down

A failure mid-apply leaves real AWS resources behind. The destroy task
is a follow-up (issue not yet filed); for now, manual cleanup:

```sh
# 1. Find the per-request workspace state key:
#    {env}/{team_id}/standalone/{rr_id}/{logical_region}/terraform.tfstate
aws s3 ls s3://forge-managed-resources-<account>/ --recursive

# 2. Materialize a destroy workspace (worker has no destroy task yet —
#    do this from a workstation with the same package version that
#    produced the original apply):
cd packages/managed_database/v1/terraform/aws/
terraform init -backend-config="bucket=forge-managed-resources-<account>" \
               -backend-config="key=<your-state-key>" \
               -backend-config="region=<your-region>"
terraform plan -destroy -out=destroy.tfplan
terraform apply destroy.tfplan
```

## Known limitations (issue #51 follow-ups, not yet filed)

These are deferred to a follow-up issue per the PR review for #51 — call
them out when demoing real-AWS so the gap is acknowledged:

1. **`/status` only surfaces `RESOURCE_REQUEST.status`.** The intermediate
   `Deployment.status` values (`planned`, `applying`, `applied`) are
   only visible in the database. A future endpoint or expanded `/status`
   payload should expose these.
2. **No max-attempts guard on Celery.** A permanently-broken request
   redelivers indefinitely; eventually the broker fills. POC posture;
   bound retries before going to staging.
3. **Apply failure → orphaned AWS resources.** Terraform writes partial
   state; we mark the deployment failed but don't auto-destroy. Operator
   must run the destroy steps above. Mention in incident response.
4. **`outputs_encrypted` is plaintext bytes** per SPEC §8.3 POC posture.
   Tightening this is part of the SPEC §8.3 encryption work, not
   provisioning.
