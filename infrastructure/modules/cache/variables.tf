variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Drives the Name tag and replication group identifier."
  type        = string
}

variable "vpc_id" {
  description = "VPC ID the Elasticache security group belongs to."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs the cache subnet group spans (one per AZ)."
  type        = list(string)
}

variable "app_security_group_id" {
  description = "Source SG for the 6379 ingress rule. Both api and worker ECS tasks attach this SG, so a single rule covers both."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the project CMK. Used for at-rest encryption of the Elasticache replication group."
  type        = string
}

variable "engine_version" {
  description = "Redis engine version. Pinned to a Redis 7.x minor — major upgrades require a parameter-group change and are deliberate."
  type        = string
  default     = "7.1"
}

variable "parameter_group_family" {
  description = "Elasticache parameter group family. Must match the major version of engine_version."
  type        = string
  default     = "redis7"
}

variable "node_type" {
  description = "Cache node type. t4g.micro is the cheapest ARM Graviton node and is plenty for a single-node POC broker."
  type        = string
  default     = "cache.t4g.micro"
}
