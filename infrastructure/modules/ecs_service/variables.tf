variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev)."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs the Fargate tasks run in (one per AZ)."
  type        = list(string)
}

variable "app_security_group_id" {
  description = "Shared app SG (created in the network module) attached to both api and worker ECS tasks. Hosts ingress 8000 from the ALB; cache and database modules also reference it as the source for their 6379/5432 ingress rules."
  type        = string
}

variable "target_group_arn" {
  description = "ALB target group ARN. The ECS service registers task IPs here."
  type        = string
}

variable "app_image" {
  description = "Fully-qualified container image URI for the Forge service."
  type        = string
}

variable "aws_region" {
  description = "AWS region. Used for the awslogs driver."
  type        = string
}

variable "environment" {
  description = "Environment short name. Passed through to the container as FORGE_ENVIRONMENT."
  type        = string
}

variable "log_retention_in_days" {
  description = "CloudWatch log group retention."
  type        = number
  default     = 7
}

variable "kms_key_arn" {
  description = "ARN of the project CMK. Used to encrypt the CloudWatch log group at rest and required for the execution role's kms:Decrypt grant on the DB master secret."
  type        = string
}

variable "master_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the DB master credentials JSON ({username, password}). The execution role gets secretsmanager:GetSecretValue scoped to this specific ARN; the task definition's secrets[] block pulls DATABASE_PASSWORD via the :password:: JSON-key path."
  type        = string
}

variable "database_host" {
  description = "Aurora cluster writer endpoint. Set as plain DATABASE_HOST env var on the task (non-secret — the endpoint isn't sensitive)."
  type        = string
}

variable "database_port" {
  description = "Aurora cluster port. Set as plain DATABASE_PORT env var on the task."
  type        = number
  default     = 5432
}

variable "database_name" {
  description = "Initial database in the Aurora cluster. Set as plain DATABASE_NAME env var on the task."
  type        = string
}

variable "database_user" {
  description = "Aurora master username. Set as plain DATABASE_USER env var on the task (non-secret — username isn't sensitive)."
  type        = string
}

variable "database_ssl_mode" {
  description = "Postgres SSL mode. `require` in cloud (Aurora terminates TLS at the cluster endpoint); `disable` for local Postgres."
  type        = string
  default     = "require"

  validation {
    condition     = contains(["disable", "prefer", "require", "verify-ca", "verify-full"], var.database_ssl_mode)
    error_message = "database_ssl_mode must be one of: disable, prefer, require, verify-ca, verify-full."
  }
}

# ─── Celery wiring (issue #54) ────────────────────────────────────────────────

variable "cache_endpoint" {
  description = "Elasticache primary endpoint DNS name. Wired into both api and worker task env as FORGE_CELERY__BROKER_URL=rediss://<endpoint>:<port>/0."
  type        = string
}

variable "cache_port" {
  description = "Elasticache primary endpoint port. Defaulted to 6379; sourced from the cache module output for honesty."
  type        = number
  default     = 6379
}

# ─── Managed-resources backend (issue #51 / E.3) ──────────────────────────────

variable "managed_resources_bucket" {
  description = "Name of the S3 bucket holding worker-produced per-resource Terraform state. Created by infrastructure/bootstrap. Wired into worker task env as FORGE_TERRAFORM__MANAGED_RESOURCES_BUCKET; worker task role gets s3:GetObject/PutObject/DeleteObject/ListBucket scoped to this bucket."
  type        = string
}

variable "managed_resources_region" {
  description = "Region the managed-resources bucket lives in. Wired into worker task env as FORGE_TERRAFORM__MANAGED_RESOURCES_REGION so the worker-rendered backend.tf points at the correct regional S3 endpoint."
  type        = string
}

variable "managed_resources_kms_key_arn" {
  description = "ARN of the CMK encrypting the managed-resources bucket. Worker task role gets kms:Encrypt/Decrypt/GenerateDataKey/DescribeKey on this key so SSE-KMS reads/writes succeed."
  type        = string
}
