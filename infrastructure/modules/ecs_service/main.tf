# Shared environment variables for both the api and worker containers. Keeping
# them in one place is load-bearing: any FORGE_* setting added here is picked
# up by both task definitions (e.g. FORGE_CELERY__BROKER_URL added in #54).
locals {
  shared_environment = [
    { name = "FORGE_APP_NAME", value = "Forge" },
    { name = "FORGE_ENVIRONMENT", value = var.environment },
    { name = "FORGE_LOG_LEVEL", value = "INFO" },
    { name = "FORGE_DATABASE__HOST", value = var.database_host },
    { name = "FORGE_DATABASE__PORT", value = tostring(var.database_port) },
    { name = "FORGE_DATABASE__NAME", value = var.database_name },
    { name = "FORGE_DATABASE__USER", value = var.database_user },
    { name = "FORGE_DATABASE__SSL_MODE", value = var.database_ssl_mode },
    # The broker URL uses rediss:// (not redis://) because the Elasticache
    # replication group has transit_encryption_enabled = true. Pydantic-settings
    # reads this verbatim as forge.settings.celery.BROKER_URL. The path /0
    # selects Redis logical database 0 — Celery's default.
    { name = "FORGE_CELERY__BROKER_URL", value = "rediss://${var.cache_endpoint}:${var.cache_port}/0" },
  ]

  shared_secrets = [
    {
      name      = "FORGE_DATABASE__PASSWORD"
      valueFrom = "${var.master_secret_arn}:password::"
    },
  ]
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name_prefix}"
  retention_in_days = var.log_retention_in_days
  # Encrypt log streams at rest with the project CMK. The CMK's key policy
  # grants the regional CloudWatch Logs service principal Encrypt/Decrypt
  # scoped (via ArnLike condition) to log groups in this account.
  kms_key_id = var.kms_key_arn
  tags       = { Name = "/ecs/${var.name_prefix}" }
}

# ─── IAM: execution role (acts before container starts) ───────────────────────
#
# The execution role is what ECS-the-orchestrator assumes to: pull the image
# from ECR, fetch secrets from Secrets Manager, decrypt them with the CMK,
# and write the awslogs driver's log streams. It is NOT exposed inside the
# running container — only the task role is.

resource "aws_iam_role" "ecs_execution" {
  name = "${var.name_prefix}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = { Name = "${var.name_prefix}-ecs-execution-role" }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_managed" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Narrowly grant the execution role read access to the DB master secret and
# decrypt access to the CMK. Resource-scoped so the execution role cannot
# read any other secret or decrypt with any other key — minimum required for
# ECS `secrets[]` to fetch FORGE_DATABASE__PASSWORD at task start.
resource "aws_iam_role_policy" "ecs_execution_db_secret" {
  name = "${var.name_prefix}-ecs-execution-db-secret"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadDbMasterSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.master_secret_arn
      },
      {
        Sid    = "DecryptDbMasterSecretWithCMK"
        Effect = "Allow"
        Action = ["kms:Decrypt"]
        # Resource-scoped to the specific CMK ARN — defense in depth even
        # though the CMK's key policy already gates access.
        Resource = var.kms_key_arn
        # Bonus: only allow decrypt when the request originates from the
        # Secrets Manager service in the same region. Prevents misuse if the
        # role gets attached to other principals later.
        Condition = {
          StringEquals = {
            "kms:ViaService" = "secretsmanager.${var.aws_region}.amazonaws.com"
          }
        }
      },
    ]
  })
}

# ─── IAM: task role (the role the running container assumes) ──────────────────
#
# Separated from the execution role per the AWS-recommended pattern. The
# running container assumes THIS role via AWS SDK calls (if it makes any).
# Currently has zero attached policies — the future provisioning API will
# add scoped grants (s3:CreateBucket on forge-managed-* etc.) here, NOT on
# the execution role.
#
# Why split:
#   - A compromised container can only do what the task role allows.
#   - Today that's nothing — even if popped, the container can't call any AWS
#     API as itself. (It still has access to env vars including
#     FORGE_DATABASE__PASSWORD, but that's the limit of what's exposed.)
#   - The execution role's secret-fetching power is NOT available to the
#     container because the container doesn't assume the execution role.

resource "aws_iam_role" "ecs_task" {
  name = "${var.name_prefix}-ecs-task-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = { Name = "${var.name_prefix}-ecs-task-role" }
}

resource "aws_ecs_cluster" "main" {
  name = var.name_prefix
  tags = { Name = var.name_prefix }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.name_prefix}-app"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  # New: explicit task role separate from execution role. The running
  # container assumes this; today it has no permissions.
  task_role_arn = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([{
    name      = "app"
    image     = var.app_image
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    # Plain (non-secret) environment variables. The FORGE_DATABASE__* values
    # implement the pydantic-settings env-var contract the app reads (see
    # src/forge/config.py — env_prefix="FORGE_DATABASE__") to construct the
    # DSN locally and in cloud — same names work in both. Only
    # FORGE_DATABASE__PASSWORD is split out into `secrets` below.
    #
    # Why host/port/user/name aren't secrets: the cluster endpoint, port,
    # database name, and master username are not sensitive. Splitting them
    # out as plain env vars saves a Secrets Manager API call per field at
    # task start (Secrets Manager has a per-second rate limit) and lets
    # operators read them via `aws ecs describe-task-definition` for
    # debugging without needing secretsmanager:GetSecretValue.
    # Shared FORGE_* vars come from locals.shared_environment; concat adds the
    # api-only HOST/PORT (the worker doesn't listen so it doesn't need them).
    environment = concat(local.shared_environment, [
      { name = "FORGE_HOST", value = "0.0.0.0" },
      { name = "FORGE_PORT", value = "8000" },
    ])

    # Secrets injected at task start by the execution role. JSON-key syntax
    # `<secret-arn>:password::` pulls only the `password` field from the
    # JSON secret value (the trailing `::` are version-stage and version-id,
    # both empty meaning "current").
    secrets = local.shared_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.app.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "app"
      }
    }
  }])

  tags = { Name = "${var.name_prefix}-app" }
}

resource "aws_ecs_service" "app" {
  name            = var.name_prefix
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [
    aws_iam_role_policy_attachment.ecs_execution_managed,
    aws_iam_role_policy.ecs_execution_db_secret,
  ]

  tags = { Name = var.name_prefix }
}

# ─── Worker: Celery consumer (issue #54) ──────────────────────────────────────
#
# Reuses the api container image and the execution role (same image needs, same
# DB-master-secret access, same log-group CMK). The worker gets its OWN task
# role so #51 can attach S3 provisioning permissions to the worker without
# also granting them to the api task. The worker shares the api's SG
# (var.app_security_group_id) so the cache module's single 6379-from-app-SG
# ingress rule covers both services.
#
# Why no load balancer: Celery workers pull from the broker; they don't listen
# on any port. No target group, no port mapping, no health-check URL.

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/ecs/${var.name_prefix}-worker"
  retention_in_days = var.log_retention_in_days
  kms_key_id        = var.kms_key_arn
  tags              = { Name = "/ecs/${var.name_prefix}-worker" }
}

resource "aws_iam_role" "ecs_worker_task" {
  name = "${var.name_prefix}-ecs-worker-task-role"

  # Same trust policy as ecs_task — both are assumed by Fargate tasks. The
  # split exists at the policy-attachment layer, not the trust layer.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  # Currently no attached policies. Issue #51 (E.3) will attach a scoped
  # s3:CreateBucket / GetBucketLocation / DeleteBucket policy here for
  # forge-managed-* buckets — that's why this role exists separately from
  # the api task role rather than being shared.

  tags = { Name = "${var.name_prefix}-ecs-worker-task-role" }
}

resource "aws_ecs_task_definition" "worker" {
  family                   = "${var.name_prefix}-worker"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "256"
  memory                   = "512"

  # Execution role is reused — it only pulls the image, fetches the DB master
  # secret, decrypts log writes. All identical to the api task. The TASK role
  # is the one that's worker-specific.
  execution_role_arn = aws_iam_role.ecs_execution.arn
  task_role_arn      = aws_iam_role.ecs_worker_task.arn

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.app_image
    essential = true

    # No portMappings — the worker doesn't listen.

    # celery -A forge.workers worker
    #   --loglevel=info
    #   --concurrency=2          (two Greenlet workers per task; the task only
    #                             gets 256 CPU units so two is the practical cap)
    #   -Q provisioning          (must match FORGE_CELERY__TASK_DEFAULT_QUEUE
    #                             in forge.config — see #53)
    command = ["celery", "-A", "forge.workers", "worker", "--loglevel=info", "--concurrency=2", "-Q", "provisioning"]

    environment = local.shared_environment
    secrets     = local.shared_secrets

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.worker.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "worker"
      }
    }
  }])

  tags = { Name = "${var.name_prefix}-worker" }
}

resource "aws_ecs_service" "worker" {
  name            = "${var.name_prefix}-worker"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  # Shared SG with the api — the cache module's 6379-from-app-SG ingress rule
  # covers both services in one place.
  network_configuration {
    subnets          = var.private_subnet_ids
    security_groups  = [var.app_security_group_id]
    assign_public_ip = false
  }

  # No load_balancer block — the worker is queue-driven.

  depends_on = [
    aws_iam_role_policy_attachment.ecs_execution_managed,
    aws_iam_role_policy.ecs_execution_db_secret,
  ]

  tags = { Name = "${var.name_prefix}-worker" }
}
