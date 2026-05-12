# CloudWatch alarms with SNS topic + email subscription.
#
# Provides runbook-grade alerting for the platform's four observable layers:
# ALB, ECS (api + worker), Aurora, and ElastiCache. All alarms publish to a
# single SNS topic; the topic fans out to each address in var.alert_emails.
#
# Subscription confirmation is manual: SNS sends a confirmation email to each
# address after apply. The subscription is pending (and no alerts are
# delivered) until the recipient clicks the confirmation link. See
# docs/observability.md for the operator runbook.
#
# var.alarms_enabled acts as a master switch so ephemeral dev stacks can be
# stood up without flooding operator inboxes. Note: setting this to false
# disables alarm actions (no SNS publish), but alarms still evaluate and will
# transition to ALARM state — they are silenced, not disabled. Use this for
# dev teardown/standup cycles where alert noise is undesirable.

# ── SNS topic ─────────────────────────────────────────────────────────────────

resource "aws_sns_topic" "alerts" {
  name              = "${var.name_prefix}-alerts"
  kms_master_key_id = var.kms_key_arn

  tags = { Name = "${var.name_prefix}-alerts" }
}

# CloudWatch needs explicit sns:Publish permission on the topic to deliver
# alarm notifications. This is a resource policy (not an IAM identity policy)
# and must be set even when the deploy role has broad SNS permissions.
data "aws_iam_policy_document" "alerts_topic_policy" {
  statement {
    sid     = "AllowCloudWatchPublish"
    effect  = "Allow"
    actions = ["sns:Publish"]
    principals {
      type        = "Service"
      identifiers = ["cloudwatch.amazonaws.com"]
    }
    resources = [aws_sns_topic.alerts.arn]
  }
}

resource "aws_sns_topic_policy" "alerts" {
  arn    = aws_sns_topic.alerts.arn
  policy = data.aws_iam_policy_document.alerts_topic_policy.json
}

resource "aws_sns_topic_subscription" "email" {
  # One subscription per email address. for_each keeps each subscription as a
  # distinct resource so adding/removing a single address doesn't destroy and
  # recreate unrelated subscriptions.
  for_each = toset(var.alert_emails)

  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = each.value
}

# ── ALB alarms ────────────────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "alb_5xx" {
  alarm_name        = "${var.name_prefix}-alb-5xx-high"
  alarm_description = "ALB target 5XX count exceeds ${var.alb_5xx_threshold} in a 5-minute window. Investigate ECS task logs."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.alb_5xx_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-alb-5xx-high" }
}

resource "aws_cloudwatch_metric_alarm" "alb_p95_response_time" {
  alarm_name        = "${var.name_prefix}-alb-p95-latency-high"
  alarm_description = "ALB p95 target response time exceeded ${var.alb_p95_response_time_threshold}s. Check ECS task CPU/memory and downstream dependencies."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/ApplicationELB"
  metric_name         = "TargetResponseTime"
  dimensions          = { LoadBalancer = var.alb_arn_suffix }
  extended_statistic  = "p95"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.alb_p95_response_time_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-alb-p95-latency-high" }
}

# ── ECS alarms (API service) ──────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ecs_api_cpu" {
  alarm_name        = "${var.name_prefix}-ecs-api-cpu-high"
  alarm_description = "ECS API service CPU utilization exceeded ${var.ecs_cpu_threshold}% for 10 minutes. Consider scaling out."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "CPUUtilization"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_api_service_name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.ecs_cpu_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-ecs-api-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "ecs_api_memory" {
  alarm_name        = "${var.name_prefix}-ecs-api-memory-high"
  alarm_description = "ECS API service memory utilization exceeded ${var.ecs_memory_threshold}% for 10 minutes. Check for memory leaks or increase task memory."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "MemoryUtilization"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_api_service_name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.ecs_memory_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-ecs-api-memory-high" }
}

resource "aws_cloudwatch_metric_alarm" "ecs_api_running_tasks" {
  alarm_name        = "${var.name_prefix}-ecs-api-tasks-low"
  alarm_description = "ECS API running task count dropped below ${var.ecs_min_running_tasks}. The API may be unavailable."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "RunningTaskCount"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_api_service_name
  }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  threshold           = var.ecs_min_running_tasks
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  tags = { Name = "${var.name_prefix}-ecs-api-tasks-low" }
}

# ── ECS alarms (worker service) ───────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "ecs_worker_cpu" {
  alarm_name        = "${var.name_prefix}-ecs-worker-cpu-high"
  alarm_description = "ECS worker service CPU utilization exceeded ${var.ecs_cpu_threshold}% for 10 minutes. Check Celery task throughput."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "CPUUtilization"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_worker_service_name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.ecs_cpu_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-ecs-worker-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "ecs_worker_memory" {
  alarm_name        = "${var.name_prefix}-ecs-worker-memory-high"
  alarm_description = "ECS worker service memory utilization exceeded ${var.ecs_memory_threshold}% for 10 minutes."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "MemoryUtilization"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_worker_service_name
  }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.ecs_memory_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-ecs-worker-memory-high" }
}

resource "aws_cloudwatch_metric_alarm" "ecs_worker_running_tasks" {
  alarm_name        = "${var.name_prefix}-ecs-worker-tasks-low"
  alarm_description = "ECS worker running task count dropped below ${var.ecs_min_running_tasks}. Async provisioning jobs will not be processed."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace   = "AWS/ECS"
  metric_name = "RunningTaskCount"
  dimensions = {
    ClusterName = var.ecs_cluster_name
    ServiceName = var.ecs_worker_service_name
  }
  statistic           = "Average"
  period              = 60
  evaluation_periods  = 2
  threshold           = var.ecs_min_running_tasks
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"

  tags = { Name = "${var.name_prefix}-ecs-worker-tasks-low" }
}

# ── RDS / Aurora alarms ───────────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name        = "${var.name_prefix}-rds-cpu-high"
  alarm_description = "Aurora cluster CPU utilization exceeded ${var.rds_cpu_threshold}% for 10 minutes."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBClusterIdentifier = var.rds_cluster_identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.rds_cpu_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-rds-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "rds_freeable_memory" {
  alarm_name        = "${var.name_prefix}-rds-memory-low"
  alarm_description = "Aurora cluster freeable memory dropped below ${var.rds_freeable_memory_threshold} bytes. Risk of OOM eviction."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/RDS"
  metric_name         = "FreeableMemory"
  dimensions          = { DBClusterIdentifier = var.rds_cluster_identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.rds_freeable_memory_threshold
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-rds-memory-low" }
}

resource "aws_cloudwatch_metric_alarm" "rds_connections" {
  alarm_name        = "${var.name_prefix}-rds-connections-high"
  alarm_description = "Aurora cluster connection count exceeded ${var.rds_connections_threshold}. Check for connection leaks or pool exhaustion."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/RDS"
  metric_name         = "DatabaseConnections"
  dimensions          = { DBClusterIdentifier = var.rds_cluster_identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.rds_connections_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-rds-connections-high" }
}

# ── ElastiCache / Redis alarms ────────────────────────────────────────────────

resource "aws_cloudwatch_metric_alarm" "cache_cpu" {
  alarm_name        = "${var.name_prefix}-cache-cpu-high"
  alarm_description = "ElastiCache CPU utilization exceeded ${var.cache_cpu_threshold}% for 10 minutes. Redis is CPU-bound; consider scaling up the node type."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/ElastiCache"
  metric_name         = "CPUUtilization"
  dimensions          = { ReplicationGroupId = var.cache_replication_group_id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.cache_cpu_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-cache-cpu-high" }
}

resource "aws_cloudwatch_metric_alarm" "cache_evictions" {
  alarm_name        = "${var.name_prefix}-cache-evictions-high"
  alarm_description = "ElastiCache eviction count exceeded ${var.cache_evictions_threshold} in 5 minutes. Redis is evicting keys due to memory pressure; investigate maxmemory-policy and data growth."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/ElastiCache"
  metric_name         = "Evictions"
  dimensions          = { ReplicationGroupId = var.cache_replication_group_id }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = var.cache_evictions_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-cache-evictions-high" }
}

resource "aws_cloudwatch_metric_alarm" "cache_connections" {
  alarm_name        = "${var.name_prefix}-cache-connections-high"
  alarm_description = "ElastiCache current connections exceeded ${var.cache_connections_threshold}. Check for connection leaks in Celery workers."
  actions_enabled   = var.alarms_enabled
  alarm_actions     = [aws_sns_topic.alerts.arn]
  ok_actions        = [aws_sns_topic.alerts.arn]

  namespace           = "AWS/ElastiCache"
  metric_name         = "CurrConnections"
  dimensions          = { ReplicationGroupId = var.cache_replication_group_id }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = var.cache_connections_threshold
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"

  tags = { Name = "${var.name_prefix}-cache-connections-high" }
}
