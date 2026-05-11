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

output "app_security_group_id" {
  description = "Security group attached to ECS tasks. Consumed by the database module's ingress rule so only app tasks can reach Aurora on 5432."
  value       = aws_security_group.app.id
}

output "task_role_arn" {
  description = "ARN of the ECS task role (the IAM role the running container assumes). Currently has no permissions; service-level grants (s3:CreateBucket etc.) land when the provisioning API does."
  value       = aws_iam_role.ecs_task.arn
}

output "execution_role_arn" {
  description = "ARN of the ECS execution role (the role ECS uses to pull images, fetch secrets, write logs — acts before the container starts)."
  value       = aws_iam_role.ecs_execution.arn
}
