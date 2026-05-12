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

variable "oidc_deploy_role_name" {
  description = "Name of the IAM role used by GitHub Actions OIDC to deploy this stack. Granted KMS administration rights via an explicit KeyAdministrators statement on the project CMK so Terraform never relies on the safety-net root statement for day-to-day key management."
  type        = string
  default     = "github-actions-ngx"
}

variable "alert_emails" {
  description = "Email addresses subscribed to the alerts SNS topic. Each address receives a confirmation email after apply and must click the link before alerts are delivered. Default is empty so stacks without monitoring configured are no-ops."
  type        = list(string)
  default     = []
}

variable "alarms_enabled" {
  description = "Master switch for all CloudWatch alarms. Set to false in ephemeral dev stacks to suppress alarm noise during teardown/standup cycles."
  type        = bool
  default     = true
}

variable "production_safety" {
  description = "Master switch for two database production-safety flags: deletion_protection and skip_final_snapshot. False during iteration so we can tear down cheaply; flip to true via tfvars before the demo (tracked by issue #20). When true, terraform destroy refuses without an explicit override and any destroy that does proceed automatically takes a final snapshot."
  type        = bool
  default     = false
}
