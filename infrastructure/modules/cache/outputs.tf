output "primary_endpoint_address" {
  description = "Primary endpoint DNS name. Wired into ECS task env as FORGE_CELERY__BROKER_URL=rediss://<endpoint>:<port>/0. The rediss:// scheme (not redis://) is required because transit_encryption_enabled = true."
  value       = aws_elasticache_replication_group.main.primary_endpoint_address
}

output "primary_port" {
  description = "Primary endpoint port. Always 6379 today, but emitted from the resource attribute so a future port change can't drift."
  value       = aws_elasticache_replication_group.main.port
}

output "security_group_id" {
  description = "Cache SG id. Currently only useful for debugging / cross-module references; not consumed in dev/main.tf today."
  value       = aws_security_group.cache.id
}
