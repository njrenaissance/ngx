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
  account_id      = data.aws_caller_identity.current.account_id
  state_bucket    = "forge-tfstate-${local.account_id}"
  state_lock_name = "forge-tfstate-lock"

  common_tags = {
    ManagedBy   = "terraform"
    Project     = "forge"
    Component   = "tfstate-backend"
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
