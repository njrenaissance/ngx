variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Used as the cluster identifier and Name tag."
  type        = string
}

variable "vpc_id" {
  description = "VPC the cluster lives in. The DB security group is created here."
  type        = string
}

variable "private_subnet_ids" {
  description = "Private subnet IDs (one per AZ) for the DB subnet group. Aurora requires subnets in at least two AZs even if we only run a single writer."
  type        = list(string)
}

variable "app_security_group_id" {
  description = "Security group ID of the ECS app tasks. The DB security group ingress allows 5432 only from this SG — no other source can reach the cluster."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the KMS CMK used to encrypt: cluster storage at rest, automated backups, Performance Insights data, and the Secrets Manager secret holding the master credentials."
  type        = string
}

variable "production_safety" {
  description = "Master switch for two production-safety flags: deletion_protection and skip_final_snapshot. False during iteration so we can tear down cheaply; flip to true before the demo (tracked by issue #20). When true, terraform destroy refuses without an explicit override, and any destroy that does proceed automatically takes a final snapshot."
  type        = bool
  default     = false
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version. Pinned so engine upgrades are explicit. Aurora Serverless v2 supports up to ~16.4 as of Jan 2026 — bump deliberately when AWS releases newer compatible versions."
  type        = string
  default     = "16.4"
}

variable "min_capacity" {
  description = "Aurora Serverless v2 minimum ACU (Aurora Capacity Unit). 0.5 is the smallest allowed; floor cost is ~$45/mo at this setting."
  type        = number
  default     = 0.5
}

variable "max_capacity" {
  description = "Aurora Serverless v2 maximum ACU. 2 is enough for a demo burst without runaway scaling cost."
  type        = number
  default     = 2.0
}

variable "backup_retention_days" {
  description = "Days to retain automated backups. 14 is a defensible middle ground — 1 day costs less but offers no real recovery window; 35 (max) is overkill at this scale."
  type        = number
  default     = 14
}

variable "database_name" {
  description = "Initial database created in the cluster. The app connects to this DB by name."
  type        = string
  default     = "forge"
}

variable "master_username" {
  description = "Aurora master username. Stored alongside the generated password in Secrets Manager."
  type        = string
  default     = "forge_admin"
}
