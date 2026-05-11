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
