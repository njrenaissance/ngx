output "repository_url" {
  description = "ECR repository URL. CI tags and pushes images here."
  value       = aws_ecr_repository.forge.repository_url
}

output "repository_name" {
  description = "ECR repository name."
  value       = aws_ecr_repository.forge.name
}

output "repository_arn" {
  description = "ECR repository ARN."
  value       = aws_ecr_repository.forge.arn
}
