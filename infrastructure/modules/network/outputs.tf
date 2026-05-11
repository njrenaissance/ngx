output "vpc_id" {
  description = "VPC ID. Consumed by the alb and ecs_service modules for security groups and target groups."
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "Public subnet IDs (one per AZ). Consumed by the alb module."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Private subnet IDs (one per AZ). Consumed by the ecs_service module."
  value       = aws_subnet.private[*].id
}
