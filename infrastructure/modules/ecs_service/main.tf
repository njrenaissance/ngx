resource "aws_security_group" "app" {
  name        = "${var.name_prefix}-app-sg"
  description = "ECS tasks inbound from ALB only"
  vpc_id      = var.vpc_id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [var.alb_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.name_prefix}-app-sg" }
}

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.name_prefix}"
  retention_in_days = var.log_retention_in_days
  tags              = { Name = "/ecs/${var.name_prefix}" }
}

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

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
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

  container_definitions = jsonencode([{
    name      = "app"
    image     = var.app_image
    essential = true

    portMappings = [{ containerPort = 8000, protocol = "tcp" }]

    # Map Forge runtime configuration through the FORGE_ env-var layer
    # (see src/forge/config.py DEFAULT_SETTINGS).
    environment = [
      { name = "FORGE_APP_NAME", value = "Forge" },
      { name = "FORGE_ENVIRONMENT", value = var.environment },
      { name = "FORGE_LOG_LEVEL", value = "INFO" },
      { name = "FORGE_HOST", value = "0.0.0.0" },
      { name = "FORGE_PORT", value = "8000" },
    ]

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
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = var.target_group_arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_iam_role_policy_attachment.ecs_execution]

  tags = { Name = var.name_prefix }
}
