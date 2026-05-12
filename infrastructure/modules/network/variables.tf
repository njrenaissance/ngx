variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Drives the Name tag on every resource in this module."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.1.0.0/16"
}

variable "az_count" {
  description = "Number of AZs to span. Public and private subnets are created in each AZ (so a value of 2 creates 2 public + 2 private subnets)."
  type        = number
  default     = 2
}

variable "alb_security_group_id" {
  description = "ALB security group ID. The shared app SG (used by both api and worker ECS tasks) ingresses port 8000 only from this SG."
  type        = string
}
