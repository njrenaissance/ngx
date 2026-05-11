variable "aws_region" {
  description = "AWS region for the Forge dev environment."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Local AWS named profile. CI overrides via TF_VAR_aws_profile=\"\" so the provider falls back to env-var credentials set by aws-actions/configure-aws-credentials."
  type        = string
  default     = "ngx-deployer"
}

variable "environment" {
  description = "Environment short name. Used as a suffix on Terraform-managed AWS resources (e.g. forge-dev-vpc, forge-staging-vpc)."
  type        = string
  default     = "dev"

  validation {
    condition     = contains(["dev", "staging", "prod"], var.environment)
    error_message = "environment must be one of: dev, staging, prod."
  }
}

variable "app_image" {
  description = "Fully-qualified container image URI for the Forge service (e.g. <account>.dkr.ecr.us-east-1.amazonaws.com/forge-dev:0.1.0). Set by CI in the deploy workflow; no default so an accidental local apply can't deploy an unintended image."
  type        = string
}
