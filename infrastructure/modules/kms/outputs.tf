output "key_arn" {
  description = "ARN of the CMK. Consumers (database module, ecs_service module) reference this to encrypt their resources at rest."
  value       = aws_kms_key.main.arn
}

output "key_id" {
  description = "Bare key ID of the CMK (UUID form). Used by AWS APIs that require key_id rather than ARN (e.g. some RDS arguments)."
  value       = aws_kms_key.main.key_id
}

output "alias_arn" {
  description = "ARN of the alias. Convenient for human-readable cross-references in AWS consoles."
  value       = aws_kms_alias.main.arn
}

output "alias_name" {
  description = "Alias name (e.g. alias/forge-dev). Convenient when an AWS argument accepts an alias instead of a key ARN."
  value       = aws_kms_alias.main.name
}
