# Engine-neutral output names. A future swap to RDS Postgres (or any other
# Postgres-compatible engine) keeps these names so callers don't change.

output "endpoint" {
  description = "Cluster writer endpoint (e.g. forge-dev-aurora.cluster-xxxxxxxx.us-east-1.rds.amazonaws.com). Failover-aware — always points at whichever instance is the current writer. The ecs_service module sets DATABASE_HOST from this value."
  value       = aws_rds_cluster.main.endpoint
}

output "reader_endpoint" {
  description = "Cluster reader endpoint, load-balanced across reader instances. Currently unused (we run a single writer; no readers). Surfaced now so the next PR that adds a reader can wire it without changing this module's contract."
  value       = aws_rds_cluster.main.reader_endpoint
}

output "port" {
  description = "Postgres port (always 5432 unless we change the cluster's port arg, which we don't). The ecs_service module sets DATABASE_PORT from this value."
  value       = aws_rds_cluster.main.port
}

output "database_name" {
  description = "Initial database created in the cluster. The ecs_service module sets DATABASE_NAME from this value."
  value       = aws_rds_cluster.main.database_name
}

output "master_username" {
  description = "Aurora master username. The ecs_service module sets DATABASE_USER from this value (plain task-def env var, not from the secret — the username isn't sensitive)."
  value       = aws_rds_cluster.main.master_username
}

output "master_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the master credentials JSON ({username, password}). The ecs_service module pulls DATABASE_PASSWORD from this secret via ECS `secrets[]` valueFrom syntax (`<arn>:password::`)."
  value       = aws_secretsmanager_secret.master.arn
}

output "db_security_group_id" {
  description = "Security group attached to the cluster. Surfaced for debugging / future cross-module reference; ingress is already configured to allow only var.app_security_group_id on 5432."
  value       = aws_security_group.db.id
}

output "cluster_arn" {
  description = "ARN of the Aurora cluster. Useful for IAM policy scoping in future PRs (e.g. backup, monitoring)."
  value       = aws_rds_cluster.main.arn
}

output "cluster_resource_id" {
  description = "Cluster resource ID — used by IAM database authentication when that lands in a future PR."
  value       = aws_rds_cluster.main.cluster_resource_id
}

output "cluster_identifier" {
  description = "Aurora cluster identifier (e.g. forge-dev-aurora). Used as the DBClusterIdentifier dimension on RDS CloudWatch metrics."
  value       = aws_rds_cluster.main.cluster_identifier
}
