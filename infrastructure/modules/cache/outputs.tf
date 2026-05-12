output "primary_endpoint_address" {
  description = "Primary endpoint DNS name. Wired into ECS task env as FORGE_CELERY__BROKER_URL=rediss://<endpoint>:<port>/0. The rediss:// scheme (not redis://) is required because transit_encryption_enabled = true."
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "primary_port" {
  description = "Primary endpoint port (hardcoded to 6379 in this module). Sourced from the resource attribute rather than a literal so the output stays honest if the port hardcode is ever lifted to a variable."
  value       = aws_elasticache_replication_group.main.port
}

output "security_group_id" {
  description = "Cache SG id. Currently only useful for debugging / cross-module references; not consumed in dev/main.tf today."
  value       = aws_security_group.cache.id
}
