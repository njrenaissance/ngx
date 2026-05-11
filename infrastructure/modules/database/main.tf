# Aurora Serverless v2 (PostgreSQL) cluster + Secrets Manager-stored
# master credentials.
#
# Module surface is engine-neutral on purpose: outputs are named `endpoint`,
# `database_name`, `master_secret_arn` etc. — not `aurora_endpoint`. A future
# swap to RDS Postgres (`db.t4g.micro`) or another Postgres-compatible engine
# is internal to this module; the dev composition's `module "database"` block
# does not change.
#
# Credential injection contract for ECS (set by the ecs_service module):
#   - Secrets Manager secret value is JSON: { "username": "...", "password": "..." }
#   - Only DATABASE_PASSWORD is pulled from Secrets Manager via ECS `secrets[]`
#   - DATABASE_HOST / DATABASE_PORT / DATABASE_NAME / DATABASE_USER / DATABASE_SSL_MODE
#     come from plain task-def env vars (ecs_service module reads `endpoint` /
#     `database_name` outputs and sets them)
#   - This split keeps the password as the only value that requires Secrets
#     Manager access, simplifies the JSON shape (no two-phase write to embed
#     the cluster endpoint), and saves a GetSecretValue call per non-password
#     field at task start.

# ─── Master password ──────────────────────────────────────────────────────────

resource "random_password" "master" {
  length  = 32
  special = true
  # Postgres allows most characters in passwords, but psycopg's libpq DSN
  # parser breaks on a few. Excluding @ : / # ? & + = % space prevents URL
  # encoding pain even though our app pulls the password from an env var
  # rather than a DSN string. (Defense-in-depth — local dev / debugging
  # may still construct DSNs.)
  override_special = "!*-_.~"
}

# ─── Secrets Manager: master credentials ──────────────────────────────────────

resource "aws_secretsmanager_secret" "master" {
  name        = "${var.name_prefix}-db-master"
  description = "Aurora master credentials for ${var.name_prefix}. JSON: { username, password }. Encrypted at rest with the project CMK."
  kms_key_id  = var.kms_key_arn

  # Short recovery window keeps teardowns clean during iteration. The default
  # is 30 days — that means a deleted secret name is unusable for a month,
  # which blocks `terraform destroy && terraform apply` cycles.
  recovery_window_in_days = 7

  tags = { Name = "${var.name_prefix}-db-master" }
}

resource "aws_secretsmanager_secret_version" "master" {
  secret_id = aws_secretsmanager_secret.master.id
  secret_string = jsonencode({
    username = var.master_username
    password = random_password.master.result
  })
}

# ─── Networking ───────────────────────────────────────────────────────────────

resource "aws_db_subnet_group" "main" {
  name       = "${var.name_prefix}-db-subnet"
  subnet_ids = var.private_subnet_ids
  # Aurora requires subnets in at least two AZs even if only a single writer
  # is provisioned today. Multi-AZ is gated by adding a reader instance later;
  # the subnet group is multi-AZ-ready from day one.

  tags = { Name = "${var.name_prefix}-db-subnet" }
}

resource "aws_security_group" "db" {
  name        = "${var.name_prefix}-db-sg"
  description = "Aurora cluster ingress 5432 from app SG only; no egress."
  vpc_id      = var.vpc_id

  ingress {
    description     = "Postgres from app tasks"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [var.app_security_group_id]
  }

  # No egress rules — the cluster never initiates outbound traffic. Aurora
  # storage/replication is internal to AWS.

  tags = { Name = "${var.name_prefix}-db-sg" }
}

# ─── Cluster + writer instance ────────────────────────────────────────────────

resource "aws_rds_cluster" "main" {
  cluster_identifier = "${var.name_prefix}-aurora"

  engine         = "aurora-postgresql"
  engine_mode    = "provisioned"
  engine_version = var.engine_version

  database_name   = var.database_name
  master_username = var.master_username
  master_password = random_password.master.result

  # Storage encryption uses the project CMK (Option-1 rubric requirement).
  # Snapshots inherit this key automatically.
  storage_encrypted = true
  kms_key_id        = var.kms_key_arn

  # Network placement: private subnets only, ingress from app SG only.
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.db.id]

  # Backups: 14-day automated retention + PITR (free except for storage).
  # `preferred_backup_window` UTC; chosen to avoid US business hours.
  backup_retention_period = var.backup_retention_days
  preferred_backup_window = "03:00-04:00"

  # Serverless v2 ACU range. min must be >= 0.5 (Aurora minimum); max
  # caps the burst cost.
  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }

  # Production safety flags driven by a single var so they flip together.
  # Default false during iteration; flip via tfvars before demo (issue #20).
  deletion_protection = var.production_safety
  skip_final_snapshot = !var.production_safety
  final_snapshot_identifier = (
    var.production_safety
    ? "${var.name_prefix}-aurora-final-${formatdate("YYYYMMDDhhmmss", timestamp())}"
    : null
  )

  # Performance Insights — free tier (7-day retention). Useful for the demo
  # if a reviewer pokes around the RDS console.
  # Note: PI is configured per-instance in cluster mode, not at cluster.

  # Lifecycle: master_password updates would force in-place replacement of
  # cluster credentials, which we never want via Terraform once the cluster
  # is running. Rotation should happen via Secrets Manager rotation Lambda
  # (out of scope for this PR). Ignore changes after first apply.
  lifecycle {
    ignore_changes = [
      master_password,
      # Final snapshot identifier embeds a timestamp; ignore so plan doesn't
      # diff on every run.
      final_snapshot_identifier,
    ]
  }

  tags = { Name = "${var.name_prefix}-aurora" }
}

resource "aws_rds_cluster_instance" "writer" {
  identifier         = "${var.name_prefix}-aurora-writer"
  cluster_identifier = aws_rds_cluster.main.id

  # `db.serverless` is the magic instance class that opts into Serverless v2.
  # The actual capacity comes from the cluster's serverlessv2_scaling_configuration.
  instance_class = "db.serverless"
  engine         = aws_rds_cluster.main.engine
  engine_version = aws_rds_cluster.main.engine_version

  publicly_accessible = false

  # Performance Insights at the instance level (free 7-day retention).
  performance_insights_enabled    = true
  performance_insights_kms_key_id = var.kms_key_arn
  # 7 days is the free tier; longer retention costs extra.
  performance_insights_retention_period = 7

  tags = { Name = "${var.name_prefix}-aurora-writer" }
}
