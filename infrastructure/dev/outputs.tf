output "alb_dns_name" {
  description = "Public DNS name of the Forge ALB. Hit this URL to reach the service."
  value       = module.alb.dns_name
}

output "ecr_repository_url" {
  description = "ECR repository URL for the Forge container image. CI tags and pushes here."
  value       = module.ecr.repository_url
}

output "ecs_cluster_name" {
  description = "ECS cluster name. Referenced by deploy.yml to trigger rolling deploys."
  value       = module.ecs_service.cluster_name
}

output "ecs_service_name" {
  description = "ECS service name. Referenced by deploy.yml for `aws ecs update-service --force-new-deployment`."
  value       = module.ecs_service.service_name
}

output "cloudwatch_log_group_name" {
  description = "CloudWatch log group ECS task logs stream to."
  value       = module.ecs_service.log_group_name
}
