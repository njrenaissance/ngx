variable "name_prefix" {
  description = "Resource name prefix (e.g. forge-dev). Applied to the SNS topic and all alarm names."
  type        = string
}

variable "kms_key_arn" {
  description = "ARN of the project CMK. Used to encrypt the SNS topic at rest (ADR-008)."
  type        = string
}

variable "alert_emails" {
  description = "List of email addresses to subscribe to the alerts SNS topic. Each address receives a confirmation email after apply — the subscription is inactive until the recipient clicks the confirmation link."
  type        = list(string)
  default     = []
}

variable "alarms_enabled" {
  description = "Master switch for all CloudWatch alarms. Set to false in ephemeral dev stacks to avoid alarm noise during teardown/standup cycles."
  type        = bool
  default     = true
}

# ── ALB inputs ────────────────────────────────────────────────────────────────

variable "alb_arn_suffix" {
  description = "ARN suffix of the Application Load Balancer (the 'app/...' portion). Used as the LoadBalancer dimension on ALB CloudWatch metrics."
  type        = string
}

# ── ECS inputs ────────────────────────────────────────────────────────────────

variable "ecs_cluster_name" {
  description = "ECS cluster name. Used as the ClusterName dimension on ECS CloudWatch metrics."
  type        = string
}

variable "ecs_api_service_name" {
  description = "ECS service name for the API task. Used as the ServiceName dimension."
  type        = string
}

variable "ecs_worker_service_name" {
  description = "ECS service name for the Celery worker task. Used as the ServiceName dimension."
  type        = string
}

# ── RDS / Aurora inputs ───────────────────────────────────────────────────────

variable "rds_cluster_identifier" {
  description = "Aurora DB cluster identifier. Used as the DBClusterIdentifier dimension on RDS CloudWatch metrics."
  type        = string
}

# ── ElastiCache inputs ────────────────────────────────────────────────────────

variable "cache_replication_group_id" {
  description = "Elasticache replication group ID. Used as the ReplicationGroupId dimension on ElastiCache CloudWatch metrics."
  type        = string
}

# ── Alarm thresholds ──────────────────────────────────────────────────────────

variable "alb_5xx_threshold" {
  description = "ALB HTTP 5XX count threshold per evaluation period. Alarm fires when the target returns this many 5XX responses in 5 minutes."
  type        = number
  default     = 10
}

variable "alb_p95_response_time_threshold" {
  description = "ALB p95 target response time threshold in seconds. Alarm fires when p95 latency exceeds this value."
  type        = number
  default     = 2
}

variable "ecs_cpu_threshold" {
  description = "ECS CPU utilization threshold (percent). Alarm fires when average CPU exceeds this value for 2 consecutive 5-minute periods."
  type        = number
  default     = 80
}

variable "ecs_memory_threshold" {
  description = "ECS memory utilization threshold (percent). Alarm fires when average memory exceeds this value for 2 consecutive 5-minute periods."
  type        = number
  default     = 80
}

variable "ecs_min_running_tasks" {
  description = "Minimum expected running task count per ECS service. Alarm fires when running tasks drop below this value."
  type        = number
  default     = 1
}

variable "rds_cpu_threshold" {
  description = "RDS CPU utilization threshold (percent). Alarm fires when average CPU exceeds this value for 2 consecutive 5-minute periods."
  type        = number
  default     = 80
}

variable "rds_freeable_memory_threshold" {
  description = "RDS freeable memory threshold in bytes. Alarm fires when freeable memory drops below this value. Default is 512 MiB — tune down for Aurora Serverless v2 at minimum ACU if alerts are noisy at baseline."
  type        = number
  default     = 536870912 # 512 MiB
}

variable "rds_connections_threshold" {
  description = "RDS database connection count threshold. Alarm fires when connections exceed this value."
  type        = number
  default     = 100
}

variable "cache_cpu_threshold" {
  description = "ElastiCache CPU utilization threshold (percent). Alarm fires when average CPU exceeds this value for 2 consecutive 5-minute periods."
  type        = number
  default     = 80
}

variable "cache_evictions_threshold" {
  description = "ElastiCache eviction count threshold per evaluation period. Non-zero evictions indicate memory pressure; alert early."
  type        = number
  default     = 100
}

variable "cache_connections_threshold" {
  description = "ElastiCache current connections threshold. Alarm fires when connection count exceeds this value."
  type        = number
  default     = 500
}
