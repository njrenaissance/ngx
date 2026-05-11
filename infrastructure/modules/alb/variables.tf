variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev)."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the ALB and target group belong to."
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs the ALB attaches to (one per AZ)."
  type        = list(string)
}
