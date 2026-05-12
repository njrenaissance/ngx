output "sns_topic_arn" {
  description = "ARN of the alerts SNS topic. Pass to additional subscribers (Lambda, PagerDuty, Slack) as a follow-up fan-out."
  value       = aws_sns_topic.alerts.arn
}

output "sns_topic_name" {
  description = "Name of the alerts SNS topic."
  value       = aws_sns_topic.alerts.name
}

output "alarm_names" {
  description = "Map of alarm logical name to CloudWatch alarm name. Useful for building dashboard widgets or composite alarms in follow-up work."
  value = {
    alb_5xx                  = aws_cloudwatch_metric_alarm.alb_5xx.alarm_name
    alb_p95_response_time    = aws_cloudwatch_metric_alarm.alb_p95_response_time.alarm_name
    ecs_api_cpu              = aws_cloudwatch_metric_alarm.ecs_api_cpu.alarm_name
    ecs_api_memory           = aws_cloudwatch_metric_alarm.ecs_api_memory.alarm_name
    ecs_api_running_tasks    = aws_cloudwatch_metric_alarm.ecs_api_running_tasks.alarm_name
    ecs_worker_cpu           = aws_cloudwatch_metric_alarm.ecs_worker_cpu.alarm_name
    ecs_worker_memory        = aws_cloudwatch_metric_alarm.ecs_worker_memory.alarm_name
    ecs_worker_running_tasks = aws_cloudwatch_metric_alarm.ecs_worker_running_tasks.alarm_name
    rds_cpu                  = aws_cloudwatch_metric_alarm.rds_cpu.alarm_name
    rds_freeable_memory      = aws_cloudwatch_metric_alarm.rds_freeable_memory.alarm_name
    rds_connections          = aws_cloudwatch_metric_alarm.rds_connections.alarm_name
    cache_cpu                = aws_cloudwatch_metric_alarm.cache_cpu.alarm_name
    cache_evictions          = aws_cloudwatch_metric_alarm.cache_evictions.alarm_name
    cache_connections        = aws_cloudwatch_metric_alarm.cache_connections.alarm_name
  }
}
