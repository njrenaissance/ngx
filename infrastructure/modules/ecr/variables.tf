variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Used as the Name tag and as the default repository name."
  type        = string
}

variable "repository_name" {
  description = "ECR repository name. Defaults to name_prefix so the dev environment's repository remains addressable as e.g. forge-dev."
  type        = string
  default     = null
}
