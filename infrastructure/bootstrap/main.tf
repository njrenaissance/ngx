# ─── Bootstrap (RUN ONCE, EVER — NOT IN CI) ───────────────────────────────────
#
# Creates the Terraform state backend that every other Terraform stack in this
# repo uses (S3 bucket + DynamoDB lock table). After this is applied once, all
# downstream `terraform apply` invocations — local OR in CI — read and write
# state through that backend.
#
# This stack itself uses LOCAL state (no `backend "s3"` block) by design: it
# provisions the backend, so it can't yet depend on it (chicken-and-egg).
#
# Operating procedure:
#   1. A platform/DevOps engineer runs `terraform apply` here ONCE manually,
#      from a workstation with valid AWS credentials for the ngx-deployer user.
#   2. The resulting `terraform.tfstate` is preserved (encrypted backup) or
#      discarded — either is recoverable via `terraform import`.
#   3. CI workflows must NEVER run this stack — it should not appear in
#      `.github/workflows/terraform.yml`. CI scope is infrastructure/dev/ only.
#   4. Re-running is technically idempotent (no resources would be recreated),
#      but it's pointless and increases blast radius for accidental drift.
#
#   terraform -chdir=infrastructure/bootstrap init
#   terraform -chdir=infrastructure/bootstrap apply

data "aws_caller_identity" "current" {}

locals {
  account_id               = data.aws_caller_identity.current.account_id
  state_bucket             = "forge-tfstate-${local.account_id}"
  state_lock_name          = "forge-tfstate-lock"
  managed_resources_bucket = "forge-managed-resources-${local.account_id}"

  common_tags = {
    ManagedBy   = "terraform"
    Project     = "forge"
    Component   = "tfstate-backend"
    Environment = "shared"
  }

  managed_resources_tags = {
    ManagedBy   = "terraform"
    Project     = "forge"
    Component   = "managed-resources-backend"
    Environment = "shared"
  }
}

# ─── S3 state bucket ──────────────────────────────────────────────────────────

resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket
  tags   = local.common_tags
}

# Block all public access — Terraform state contains secrets (resource IDs,
# password hashes, raw outputs) and must never be publicly readable.
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Versioning enables point-in-time recovery if a bad apply corrupts state.
# Critical for state files because there's no other source of truth.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Server-side encryption with AWS-managed keys (AES-256). We can switch to a
# customer-managed KMS key in a later PR once we have a forge-managed CMK.
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ─── DynamoDB lock table ──────────────────────────────────────────────────────

resource "aws_dynamodb_table" "tfstate_lock" {
  name         = local.state_lock_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = local.common_tags
}

# ─── Outputs ──────────────────────────────────────────────────────────────────

output "state_bucket_name" {
  description = "Name of the S3 bucket hosting Terraform remote state. Reference this in every downstream backend.tf."
  value       = aws_s3_bucket.tfstate.bucket
}

output "state_bucket_arn" {
  value = aws_s3_bucket.tfstate.arn
}

output "state_lock_table_name" {
  description = "Name of the DynamoDB table that serializes Terraform state operations."
  value       = aws_dynamodb_table.tfstate_lock.name
}

output "state_lock_table_arn" {
  value = aws_dynamodb_table.tfstate_lock.arn
}

output "aws_account_id" {
  description = "The AWS account ID Terraform is currently authenticated against."
  value       = local.account_id
}

# ─── Managed-resources backend (issue #51 / E.3) ──────────────────────────────
#
# Holds two things, both produced by the *provisioning worker* (not by this
# bootstrap stack):
#   1. Terraform state files for per-request workspaces — keyed at
#      `{env}/{team_id}/standalone/{rr_id}/{logical_region}/terraform.tfstate`
#      (see src/forge/workers/workspace.py for the key shape).
#   2. (Future) artifacts the worker emits per provisioned resource.
#
# Why this is *separate* from the platform `forge-tfstate-<account>` bucket
# above: the platform bucket is operator-managed (only humans + the OIDC
# deploy role can write). The managed-resources bucket is *worker-writable*
# at runtime — the worker's ECS task role gets s3:PutObject scoped here so
# it can save per-request state. Granting the worker access to the platform
# state bucket would let a malicious request corrupt the foundation
# everything else stands on.
#
# Locking: TF 1.10+ S3-native `use_lockfile = true` is configured per
# backend (see workspace.py's backend.tf template). No DynamoDB lock table
# — each per-request workspace has exactly one writer (one Celery task),
# so the contention DynamoDB solves doesn't apply here.

resource "aws_kms_key" "managed_resources" {
  description             = "CMK for the forge managed-resources S3 bucket and the per-resource Elasticache/RDS encryption keys."
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = local.managed_resources_tags

  # No `policy = ...` — the default policy grants root account full access,
  # which lets IAM role-attached policies (the worker's S3+KMS grants in
  # infrastructure/modules/ecs_service) take effect without an explicit
  # key-policy entry per principal. Tightening to a custom policy is a
  # hardening follow-up; for the POC the default is intentional.
}

resource "aws_kms_alias" "managed_resources" {
  # Alias name matches the packages/managed_database/v1 data lookup
  # (`alias/forge-${var.environment}-managed-resources`). The "shared"
  # value here mirrors the bootstrap stack's account-level scope; per-env
  # aliases would require this stack to know about each environment, which
  # it deliberately does not.
  name          = "alias/forge-shared-managed-resources"
  target_key_id = aws_kms_key.managed_resources.key_id
}

resource "aws_s3_bucket" "managed_resources" {
  bucket = local.managed_resources_bucket
  tags   = local.managed_resources_tags
}

resource "aws_s3_bucket_public_access_block" "managed_resources" {
  bucket                  = aws_s3_bucket.managed_resources.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "managed_resources" {
  bucket = aws_s3_bucket.managed_resources.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "managed_resources" {
  bucket = aws_s3_bucket.managed_resources.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.managed_resources.arn
    }
    # Reduces KMS call volume — one data key per (bucket, prefix) instead of
    # one per object. Safe for state files because they're small and the
    # cryptographic boundary the CMK enforces is per-bucket, not per-object.
    bucket_key_enabled = true
  }
}

# Deny any access that isn't using TLS. SPEC Appendix B rule 1 forbids
# leaking cloud coordinates over plaintext; this is the network-layer
# enforcement for the bucket.
resource "aws_s3_bucket_policy" "managed_resources_tls_only" {
  bucket = aws_s3_bucket.managed_resources.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "DenyInsecureTransport"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.managed_resources.arn,
        "${aws_s3_bucket.managed_resources.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}

# Noncurrent versions expire after 90 days so a runaway versioning history
# from frequent state writes can't grow forever. Multipart aborts after
# 7d so an interrupted upload doesn't pin storage cost indefinitely.
resource "aws_s3_bucket_lifecycle_configuration" "managed_resources" {
  bucket = aws_s3_bucket.managed_resources.id

  rule {
    id     = "expire-noncurrent"
    status = "Enabled"

    filter {}

    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

output "managed_resources_bucket_name" {
  description = "Name of the S3 bucket holding worker-produced per-resource Terraform state. Reference in infrastructure/dev as FORGE_TERRAFORM__MANAGED_RESOURCES_BUCKET on the worker task."
  value       = aws_s3_bucket.managed_resources.bucket
}

output "managed_resources_bucket_arn" {
  value = aws_s3_bucket.managed_resources.arn
}

output "managed_resources_bucket_region" {
  description = "Region the managed-resources bucket lives in. Passed to the worker as FORGE_TERRAFORM__MANAGED_RESOURCES_REGION so backend.tf renders correctly."
  value       = var.aws_region
}

output "managed_resources_kms_key_arn" {
  description = "ARN of the CMK encrypting the managed-resources bucket. Worker task role grants kms:Encrypt/Decrypt/GenerateDataKey on this key."
  value       = aws_kms_key.managed_resources.arn
}

output "managed_resources_kms_key_alias" {
  description = "Alias name (e.g. alias/forge-shared-managed-resources) referenced by packages/*/v*/terraform/aws/main.tf via a data \"aws_kms_key\" lookup."
  value       = aws_kms_alias.managed_resources.name
}

# ─── Per-package managed-resources IAM identities (issue #86) ────────────────
#
# One IAM role per managed-resource package type. The worker (running with
# the dev stack's `forge-ecs-worker-task-role`, which itself has only S3+KMS
# on the managed-resources bucket and sts:AssumeRole on these per-package
# roles) assumes the matching role per request before invoking
# `terraform apply`. A compromised worker is bounded to one package type's
# AWS API surface — see ADR-018.
#
# Trust principal is the worker task role expressed as a *constructed-by-name*
# ARN, not a reference to the role resource itself. The role lives in the
# dev stack (infrastructure/modules/ecs_service), but this bootstrap stack
# runs first and can't pull a remote state. IAM does not validate principal
# ARN existence at policy-create time, so the constructed string works
# without a circular plan dependency. If the worker role is renamed, this
# trust string must be updated in lockstep.

locals {
  # Worker task role names follow the per-env naming convention enforced by
  # infrastructure/modules/ecs_service: `forge-<env>-ecs-worker-task-role`.
  # The bootstrap stack is account-level (no env concept), so we construct
  # one trust ARN per environment listed in var.worker_task_role_environments
  # — the role(s) need not exist yet (constructed-by-name principals).
  #
  # !!! CROSS-FILE INVARIANT !!! The string template below MUST stay in sync
  # with infrastructure/modules/ecs_service/main.tf's `aws_iam_role.ecs_worker_task`
  # `name` attribute (currently `${var.name_prefix}-ecs-worker-task-role` with
  # name_prefix = "forge-<env>"). If the ECS module renames the worker role,
  # this trust string becomes stale and sts:AssumeRole silently fails at
  # first real request. There is no automated check — renaming requires
  # updating both files in the same PR.
  worker_task_role_arns = [
    for env in var.worker_task_role_environments :
    "arn:aws:iam::${local.account_id}:role/forge-${env}-ecs-worker-task-role"
  ]
}

# Scope of this issue: only the `managed_database` role. Other packages
# (managed_cache, etc.) get their own role with their own per-API-action
# policy when the package itself lands — keeps PR scope reviewable.

resource "aws_iam_role" "managed_database" {
  name = "forge-managed-database-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowWorkerTaskRoleToAssume"
      Effect    = "Allow"
      Principal = { AWS = local.worker_task_role_arns }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.managed_resources_tags
}

resource "aws_iam_role_policy" "managed_database" {
  name = "forge-managed-database-policy"
  role = aws_iam_role.managed_database.id

  # Permissions narrowly scoped to what packages/managed_database/v1/terraform
  # actually issues against AWS. Three groups:
  #   1. RDS lifecycle — Resource = "*" because many rds:Describe* / tagging
  #      APIs do not support ARN-level scoping. Lifecycle (Create/Modify/Delete)
  #      is bounded operationally by ARN naming (forge-*) and by the fact
  #      that this role is only ever assumed for managed_database workspaces.
  #   2. KMS — scoped to the managed-resources CMK only.
  #   3. EC2 / IAM:PassRole — narrow Describe* for terraform plan; PassRole
  #      conditioned on the RDS enhanced-monitoring service.
  #   4. SecretsManager — RDS master-secret lifecycle, scoped to forge-* names.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RdsLifecycle"
        Effect = "Allow"
        Action = [
          "rds:CreateDBCluster",
          "rds:CreateDBInstance",
          "rds:CreateDBSubnetGroup",
          "rds:CreateDBParameterGroup",
          "rds:CreateDBClusterParameterGroup",
          "rds:ModifyDBCluster",
          "rds:ModifyDBInstance",
          "rds:ModifyDBSubnetGroup",
          "rds:DeleteDBCluster",
          "rds:DeleteDBInstance",
          "rds:DeleteDBSubnetGroup",
          "rds:DeleteDBParameterGroup",
          "rds:DeleteDBClusterParameterGroup",
          "rds:Describe*",
          "rds:ListTagsForResource",
          "rds:AddTagsToResource",
          "rds:RemoveTagsFromResource",
        ]
        Resource = "*"
      },
      {
        Sid    = "KmsForManagedResources"
        Effect = "Allow"
        Action = [
          "kms:CreateGrant",
          "kms:RevokeGrant",
          "kms:DescribeKey",
          "kms:GenerateDataKey",
          "kms:Encrypt",
          "kms:Decrypt",
        ]
        Resource = aws_kms_key.managed_resources.arn
      },
      {
        Sid    = "Ec2NetworkLookups"
        Effect = "Allow"
        Action = [
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeSubnets",
          "ec2:DescribeVpcs",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeAccountAttributes",
        ]
        # ec2:Describe* does not support resource-level permissions.
        Resource = "*"
      },
      {
        Sid      = "PassMonitoringRole"
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = "*"
        Condition = {
          # Only the RDS enhanced-monitoring service can be the target of any
          # PassRole this role grants — terraform's RDS provisioning needs
          # this; no other PassRole path is enabled.
          StringEquals = { "iam:PassedToService" = "monitoring.rds.amazonaws.com" }
        }
      },
      {
        Sid    = "SecretsManagerForRdsMasterSecret"
        Effect = "Allow"
        Action = [
          "secretsmanager:CreateSecret",
          "secretsmanager:DescribeSecret",
          "secretsmanager:GetSecretValue",
          "secretsmanager:DeleteSecret",
          "secretsmanager:TagResource",
        ]
        Resource = "arn:aws:secretsmanager:*:*:secret:forge-*"
      },
    ]
  })
}

output "managed_database_role_arn" {
  description = "ARN of the per-package IAM role the worker assumes before applying managed_database packages. Plumbed through infrastructure/dev to the worker ECS task as FORGE_AWS__MANAGED_RESOURCES_ROLE_ARNS."
  value       = aws_iam_role.managed_database.arn
}

output "managed_database_role_name" {
  description = "Name of the per-package IAM role (matches the assume_role_policy ARN constructed in this stack)."
  value       = aws_iam_role.managed_database.name
}
