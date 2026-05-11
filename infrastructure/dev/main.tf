data "aws_availability_zones" "available" { state = "available" }

locals {
  name_prefix = "forge-${var.environment}"
}

# ─── VPC ─────────────────────────────────────────────────────────────────────

resource "aws_vpc" "main" {
  cidr_block           = "10.1.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${local.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-igw" }
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = { Name = "${local.name_prefix}-public-${count.index + 1}" }
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = { Name = "${local.name_prefix}-private-${count.index + 1}" }
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = { Name = "${local.name_prefix}-nat-eip" }
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = { Name = "${local.name_prefix}-nat" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = { Name = "${local.name_prefix}-public-rt" }
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = { Name = "${local.name_prefix}-private-rt" }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ─── Security Groups ──────────────────────────────────────────────────────────

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB inbound HTTP"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-alb-sg" }
}

resource "aws_security_group" "app" {
  name        = "${local.name_prefix}-app-sg"
  description = "ECS tasks inbound from ALB only"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${local.name_prefix}-app-sg" }
}

# ─── ALB ─────────────────────────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
  tags               = { Name = "${local.name_prefix}-alb" }
}

resource "aws_lb_target_group" "app" {
  name        = "${local.name_prefix}-tg"
  port        = 8000
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # ALB probes the readiness endpoint so it stops routing to a task that's
  # crashed or restarting. Liveness is the orchestrator's concern (ECS task
  # health) and lives at /livez.
  health_check {
    path                = "/readyz"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  tags = { Name = "${local.name_prefix}-tg" }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}

# ─── ECR ──────────────────────────────────────────────────────────────────────

resource "aws_ecr_repository" "forge" {
  name                 = local.name_prefix
  image_tag_mutability = "MUTABLE"

  # Scan images on push for known CVEs. Findings appear in the AWS console
  # under ECR → Repositories → forge-dev → Images.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = local.name_prefix }
}

resource "aws_ecr_lifecycle_policy" "forge" {
  repository = aws_ecr_repository.forge.name

  # Rule ordering matters — ECR evaluates by ascending rulePriority and the
  # first match wins. Specific ephemeral-tag patterns come before broad rules.
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged images after 1 day (cleans up half-finished pushes)"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Expire ephemeral build tags (sha-*, pr-*) after 30 days"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["sha-", "pr-"]
          countType     = "sinceImagePushed"
          countUnit     = "days"
          countNumber   = 30
        }
        action = { type = "expire" }
      }
    ]
  })
}

# ─── ECS ─────────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name_prefix}"
  retention_in_days = 7
  tags              = { Name = "/ecs/${local.name_prefix}" }
}

resource "aws_iam_role" "ecs_execution" {
  name = "${local.name_prefix}-ecs-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })

  tags = { Name = "${local.name_prefix}-ecs-execution-role" }
}

resource "aws_iam_role_policy_attachment" "ecs_execution" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_ecs_cluster" "main" {
  name = local.name_prefix
  tags = { Name = local.name_prefix }
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${local.name_prefix}-app"
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

  tags = { Name = "${local.name_prefix}-app" }
}

resource "aws_ecs_service" "app" {
  name            = local.name_prefix
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  depends_on = [aws_iam_role_policy_attachment.ecs_execution]

  tags = { Name = local.name_prefix }
}

# ─── Outputs ─────────────────────────────────────────────────────────────────

output "alb_dns_name" {
  description = "Public DNS name of the Forge ALB. Hit this URL to reach the service."
  value       = aws_lb.main.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for the Forge container image. CI tags and pushes here."
  value       = aws_ecr_repository.forge.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name. Referenced by deploy.yml to trigger rolling deploys."
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "ECS service name. Referenced by deploy.yml for `aws ecs update-service --force-new-deployment`."
  value       = aws_ecs_service.app.name
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group ECS task logs stream to."
  value       = aws_cloudwatch_log_group.app.name
}
