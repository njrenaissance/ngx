output "dns_name" {
  description = "Public DNS name of the ALB."
  value       = aws_lb.main.dns_name
}

output "target_group_arn" {
  description = "Target group ARN. Consumed by ecs_service to wire load balancing."
  value       = aws_lb_target_group.app.arn
}

output "security_group_id" {
  description = "ALB security group ID. Consumed by ecs_service so the app security group can ingress traffic from the ALB."
  value       = aws_security_group.alb.id
}
