output "alb_dns_name" {
  description = "Public DNS name of the Forge ALB. Hit this URL to reach the service."
  value       = module.alb.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for the Forge container image. CI tags and pushes here."
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name. Referenced by deploy.yml to trigger rolling deploys."
  value       = module.ecs_service.cluster_name
}

output "ecs_service_name" {
  description = "ECS service name. Referenced by deploy.yml for `aws ecs update-service --force-new-deployment`."
  value       = module.ecs_service.service_name
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group ECS task logs stream to."
  value       = module.ecs_service.log_group_name
}

output "database_endpoint" {
  description = "Aurora cluster writer endpoint. Connect via SSM port-forward + psql for ad-hoc debugging; never expose publicly."
  value       = module.database.endpoint
}

output "database_name" {
  description = "Initial database in the Aurora cluster."
  value       = module.database.database_name
}

output "database_master_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the DB master credentials JSON. Read with: aws secretsmanager get-secret-value --secret-id <arn> --query SecretString --output text"
  value       = module.database.master_secret_arn
}

output "kms_key_arn" {
  description = "ARN of the project CMK. Useful for SSM session manager debugging / cross-reference."
  value       = module.kms.key_arn
}

output "kms_alias_name" {
  description = "Human-readable KMS alias (e.g. alias/forge-dev)."
  value       = module.kms.alias_name
}

# ─── Celery broker + worker (issue #54) ───────────────────────────────────────

output "cache_endpoint" {
  description = "Elasticache primary endpoint. Useful for ad-hoc redis-cli (over a bastion or SSM session) when debugging broker state."
  value       = module.cache.primary_endpoint_address
}

output "ecs_worker_service_name" {
  description = "ECS service name for the Celery worker. Referenced by deploy.yml-style `aws ecs update-service --force-new-deployment` runs when redeploying just the worker."
  value       = module.ecs_service.worker_service_name
}

output "ecs_worker_log_group_name" {
  description = "CloudWatch log group the worker streams to. Grep here for `celery@... ready` and `Connected to rediss://...` on healthy startup."
  value       = module.ecs_service.worker_log_group_name
}
