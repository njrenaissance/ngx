variable "db_engine" {
  description = "Database engine. One of: postgres, mysql. Mapped from request.config.engine."
  type        = string
}

variable "db_size" {
  description = "Logical size class. One of: small, medium, large, xlarge. Mapped from request.config.size."
  type        = string
}

variable "db_storage_gb" {
  description = "Initial allocated storage in gigabytes. Mapped from request.config.storage_gb."
  type        = number
}

variable "managed_resources_role_arn" {
  description = <<-EOT
    ARN of the per-package IAM role the worker has assumed before invoking
    `terraform apply`. Empty string skips the provider's `assume_role` block
    so terraform falls back to ambient AWS credentials — the local-dev path.
    Injected by the worker (src/forge/workers/workspace.py) outside the
    terraform_variable_map; see ADR-018.
  EOT
  type        = string
  default     = ""
}
