output "cluster_name" {
  description = "ECS cluster name. Referenced by deploy.yml to trigger rolling deploys."
  value       = aws_ecs_cluster.main.name
}

output "service_name" {
  description = "ECS service name. Referenced by deploy.yml for `aws ecs update-service --force-new-deployment`."
  value       = aws_ecs_service.app.name
}

output "log_group_name" {
  description = "CloudWatch log group ECS task logs stream to."
  value       = aws_cloudwatch_log_group.app.name
}
