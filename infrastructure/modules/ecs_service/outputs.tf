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

output "task_role_arn" {
  description = "ARN of the ECS task role (the IAM role the running container assumes). Currently has no permissions; service-level grants (s3:CreateBucket etc.) land when the provisioning API does."
  value       = aws_iam_role.ecs_task.arn
}

output "execution_role_arn" {
  description = "ARN of the ECS execution role (the role ECS uses to pull images, fetch secrets, write logs — acts before the container starts)."
  value       = aws_iam_role.ecs_execution.arn
}

# ─── Worker (issue #54) ───────────────────────────────────────────────────────

output "worker_service_name" {
  description = "ECS service name for the Celery worker. Useful for `aws ecs update-service --force-new-deployment` runs that need to redeploy the worker without touching the api."
  value       = aws_ecs_service.worker.name
}

output "worker_log_group_name" {
  description = "CloudWatch log group the worker container streams to. Search here for `celery@... ready` and `Connected to rediss://...` on healthy startup."
  value       = aws_cloudwatch_log_group.worker.name
}

output "worker_task_role_arn" {
  description = "ARN of the worker task role. Currently has no attached policies; #51 (E.3) attaches scoped S3 provisioning permissions here."
  value       = aws_iam_role.ecs_worker_task.arn
}
