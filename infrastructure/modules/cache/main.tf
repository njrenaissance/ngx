# Elasticache for Redis — Celery broker + result-routing layer.
#
# Single-node POC topology per issue #54: one cache cluster, no automatic
# failover, no multi-AZ replica. Encryption in transit (rediss://) and at
# rest (project CMK) are mandatory. AUTH token is intentionally omitted —
# transit encryption plus the SG-scoped 6379 ingress is the documented
# POC posture; AUTH lands when staging is provisioned (see issue #54 open
# follow-ups).
#
# Why this module exists separately from ecs_service:
#   - ecs_service needs the cache endpoint as an env var (FORGE_CELERY__BROKER_URL).
#   - cache needs the app SG as an ingress source.
# Both consume the app SG from the network module to avoid a module-level
# cycle (see modules/network/main.tf for the relocation rationale).

resource "aws_elasticache_subnet_group" "main" {
  name        = "${var.name_prefix}-cache-subnet"
  description = "Private subnets for the ${var.name_prefix} Elasticache replication group."
  subnet_ids  = var.private_subnet_ids

  tags = { Name = "${var.name_prefix}-cache-subnet" }
}

resource "aws_security_group" "cache" {
  name        = "${var.name_prefix}-cache-sg"
  description = "Elasticache cluster ingress 6379 from app SG only; no egress."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from forge app + worker tasks"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [var.app_security_group_id]
  }

  # No egress rules — the cache never initiates outbound traffic. All
  # replication/management traffic is internal to AWS.

  tags = { Name = "${var.name_prefix}-cache-sg" }
}

resource "aws_elasticache_parameter_group" "main" {
  name        = "${var.name_prefix}-cache-params"
  family      = var.parameter_group_family
  description = "Redis parameters for ${var.name_prefix} Celery broker. Defaults are fine for the POC."

  tags = { Name = "${var.name_prefix}-cache-params" }
}

resource "aws_elasticache_replication_group" "main" {
  replication_group_id = "${var.name_prefix}-cache"
  description          = "Forge ${var.name_prefix} Celery broker (single-node POC)"

  engine         = "redis"
  engine_version = var.engine_version
  node_type      = var.node_type
  port           = 6379

  # Single node — multi-AZ replication and failover are explicitly deferred
  # per issue #54. Flipping num_cache_clusters > 1 also requires enabling
  # automatic_failover, so these two flags move together.
  num_cache_clusters         = 1
  automatic_failover_enabled = false
  multi_az_enabled           = false

  parameter_group_name = aws_elasticache_parameter_group.main.name
  subnet_group_name    = aws_elasticache_subnet_group.main.name
  security_group_ids   = [aws_security_group.cache.id]

  # Mandatory encryption. The CMK grant is created implicitly when the
  # principal running terraform has kms:CreateGrant on the key — the OIDC
  # deploy role already has admin actions on the project CMK, so no key
  # policy edit is needed for Elasticache.
  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  kms_key_id                 = var.kms_key_arn

  # No AUTH token for POC — see header comment. checkov:skip=CKV_AWS_31
  # explicitly suppresses the AUTH-required finding; revisit in staging.
  auth_token = null

  # dev convenience: apply parameter/engine changes immediately rather than
  # waiting for the maintenance window. Flip to false alongside production_safety
  # if/when this module is used outside dev.
  apply_immediately = true

  tags = { Name = "${var.name_prefix}-cache" }
}
