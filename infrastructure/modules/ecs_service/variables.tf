variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the app security group belongs to."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs the Fargate task runs in (one per AZ)."
  type        = list(string)
}

variable "alb_security_group_id" {
  description = "ALB security group ID. The app security group ingresses port 8000 only from this SG."
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
