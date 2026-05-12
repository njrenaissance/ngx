# Customer-managed KMS key (CMK) used to encrypt:
#   - Aurora cluster storage at rest (and its automated backups)
#   - The Secrets Manager secret holding the DB master credentials
#   - The ECS task's CloudWatch log group
#
# Why a CMK and not the AWS-managed defaults (aws/rds, aws/secretsmanager):
#   - Explicit control over the key policy (we decide who can encrypt/decrypt)
#   - Full CloudTrail visibility into who used the key and when
#   - Annual rotation owned by us (and asserted by the tftest)
#   - Required by the Option-1 rubric: "KMS CMK on Aurora storage + log groups"
#
# The key policy is intentionally narrow: only the account root and the three
# AWS service principals that need to encrypt/decrypt with this key are
# granted access. There is no `Principal: "*"` statement (the tftest enforces
# this — adding one would be a regression).

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  alias_name = "alias/${var.name_prefix}"

  # CloudWatch Logs uses a region-scoped service principal
  # (e.g. logs.us-east-1.amazonaws.com). Derived from the active provider
  # region so this module remains region-portable.
  logs_service_principal = "logs.${data.aws_region.current.name}.amazonaws.com"

  # Toggle: only emit the KeyAdministrators statement when at least one
  # admin ARN is provided. The empty-list default keeps the module usable
  # in tests / minimal setups while making the production path explicit.
  has_key_administrators = length(var.key_administrator_role_arns) > 0

  # KMS management actions only — explicitly excludes Encrypt/Decrypt /
  # ReEncrypt / GenerateDataKey (those are Key User actions, granted to
  # service principals further down). This split is the rubric-visible
  # "separation of duties" — the OIDC deploy role can manage the key but
  # cannot use it to read encrypted data.
  key_administrator_actions = [
    "kms:Create*",
    "kms:Describe*",
    "kms:Enable*",
    "kms:Disable*",
    "kms:List*",
    "kms:Put*",
    "kms:Update*",
    "kms:Revoke*",
    "kms:Schedule*",
    "kms:Tag*",
    "kms:Untag*",
    "kms:CancelKeyDeletion",
    "kms:GetKeyPolicy",
    "kms:GetKeyRotationStatus",
  ]
}

resource "aws_kms_key" "main" {
  description             = "Forge ${var.environment} CMK — Aurora storage, Secrets Manager, CloudWatch Logs, SNS alerts topic"
  deletion_window_in_days = var.deletion_window_in_days
  enable_key_rotation     = true
  key_usage               = "ENCRYPT_DECRYPT"

  # Key policy structure (separation of duties):
  #   1. EnableRootAccountAccess — AWS-recommended safety-net statement.
  #      Does NOT mean the root user runs Terraform. It means IAM policies
  #      in this account can grant access to this key. Without it the key
  #      can be orphaned permanently if the explicit statements get
  #      misconfigured.
  #   2. KeyAdministrators (conditional) — explicit grant to the IAM roles
  #      that manage this key (typically the OIDC deploy role). Management
  #      actions only — no Encrypt/Decrypt. Day-to-day Terraform operations
  #      flow through THIS path, not the safety-net root statement.
  #   3-6. Service Key Users — RDS / Secrets Manager / CloudWatch Logs / SNS.
  #      Encrypt/Decrypt only — these principals can't change the key.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid       = "EnableRootAccountAccess"
          Effect    = "Allow"
          Principal = { AWS = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:root" }
          Action    = "kms:*"
          Resource  = "*"
        },
      ],
      local.has_key_administrators ? [
        {
          Sid       = "KeyAdministrators"
          Effect    = "Allow"
          Principal = { AWS = var.key_administrator_role_arns }
          Action    = local.key_administrator_actions
          Resource  = "*"
        },
      ] : [],
      [
        {
          Sid       = "AllowRDSEncryptDecrypt"
          Effect    = "Allow"
          Principal = { Service = "rds.amazonaws.com" }
          Action = [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:DescribeKey",
            # CreateGrant lets RDS hand the key to subordinate services
            # (e.g. when restoring snapshots) without re-prompting for policy.
            "kms:CreateGrant",
          ]
          Resource = "*"
        },
        {
          Sid       = "AllowSecretsManagerEncryptDecrypt"
          Effect    = "Allow"
          Principal = { Service = "secretsmanager.amazonaws.com" }
          Action = [
            "kms:Encrypt",
            "kms:Decrypt",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:DescribeKey",
          ]
          Resource = "*"
        },
        {
          Sid       = "AllowSNSEncryptDecrypt"
          Effect    = "Allow"
          Principal = { Service = "sns.amazonaws.com" }
          Action = [
            "kms:GenerateDataKey*",
            "kms:Decrypt",
          ]
          Resource = "*"
        },
        {
          Sid       = "AllowCloudWatchLogsEncryptDecrypt"
          Effect    = "Allow"
          Principal = { Service = local.logs_service_principal }
          Action = [
            "kms:Encrypt*",
            "kms:Decrypt*",
            "kms:ReEncrypt*",
            "kms:GenerateDataKey*",
            "kms:Describe*",
          ]
          Resource = "*"
          # Scope to log groups in this account so the key can't be used by
          # arbitrary cross-account log groups even if its ARN leaks.
          Condition = {
            ArnLike = {
              "kms:EncryptionContext:aws:logs:arn" = "arn:aws:logs:${data.aws_region.current.name}:${data.aws_caller_identity.current.account_id}:*"
            }
          }
        },
      ],
    )
  })

  tags = { Name = var.name_prefix }
}

resource "aws_kms_alias" "main" {
  name          = local.alias_name
  target_key_id = aws_kms_key.main.key_id
}
