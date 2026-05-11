variable "aws_region" {
  description = "AWS region where the Terraform state backend lives."
  type        = string
  default     = "us-east-1"
}

variable "aws_profile" {
  description = "Local AWS named profile used by Terraform. CI overrides this with TF_VAR_aws_profile=\"\" so the provider falls back to env-var credentials set by aws-actions/configure-aws-credentials."
  type        = string
  default     = "ngx-deployer"
}
