locals {
  repository_name = coalesce(var.repository_name, var.name_prefix)
}

resource "aws_ecr_repository" "forge" {
  name                 = local.repository_name
  image_tag_mutability = "MUTABLE"

  # Scan images on push for known CVEs. Findings appear in the AWS console
  # under ECR → Repositories → forge-dev → Images.
  image_scanning_configuration {
    scan_on_push = true
  }

  tags = { Name = var.name_prefix }
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
