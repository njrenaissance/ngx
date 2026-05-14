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

variable "worker_task_role_environments" {
  description = <<-EOT
    List of environment short names whose worker task role
    (`forge-<env>-ecs-worker-task-role`, created in the per-env stack under
    infrastructure/modules/ecs_service) is allowed to sts:AssumeRole into
    each per-package managed-resources role created by this bootstrap stack.

    Each entry yields one constructed-by-name ARN added to the role's trust
    policy. IAM does not validate principal-ARN existence at policy-create
    time, so the role can be trusted before the per-env stack creates it,
    breaking the circular plan dependency between bootstrap and per-env.

    Defaults to ["dev"] for the current POC scope. Add "staging" / "prod" /
    additional environment short-names here when those stacks come online.
  EOT
  type        = list(string)
  default     = ["dev"]
}
