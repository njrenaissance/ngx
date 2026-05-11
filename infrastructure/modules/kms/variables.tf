variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Used as the Name tag and in the alias suffix."
  type        = string
}

variable "environment" {
  description = "Environment name (e.g. dev, prod). Used in the alias name (alias/forge-<environment>) so consoles and Terraform can find the key by a stable, human-readable handle."
  type        = string
}

variable "key_administrator_role_arns" {
  description = "ARNs of IAM roles granted KMS management actions on this key (Create/Describe/Enable/Disable/List/Put/Update/Revoke/Schedule/Tag actions). Explicitly does NOT grant Encrypt/Decrypt — that's the Key User path. Typically the CI deploy role (e.g. arn:aws:iam::ACCOUNT:role/github-actions-ngx) so Terraform can manage the key without relying on the safety-net root statement. If empty, no KeyAdministrators statement is emitted and management depends solely on the root statement (not recommended for production)."
  type        = list(string)
  default     = []
}

variable "deletion_window_in_days" {
  description = "Number of days AWS waits before permanently deleting the key after a ScheduleKeyDeletion call. Min 7 (AWS minimum), max 30. Short window keeps teardowns clean during iteration; the demo posture should leave at the default."
  type        = number
  default     = 7

  validation {
    condition     = var.deletion_window_in_days >= 7 && var.deletion_window_in_days <= 30
    error_message = "deletion_window_in_days must be between 7 and 30 (AWS-imposed range)."
  }
}
