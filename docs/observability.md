# Observability: CloudWatch Alarms & SNS Alerts

The platform uses CloudWatch metric alarms to notify operators when key signals
breach thresholds. All alarms publish to a single SNS topic; the topic fans out
to each email address in `var.alert_emails`.

## Email subscription confirmation

SNS requires each subscriber to confirm their subscription before alerts are
delivered. After `terraform apply`:

1. Every address in `alert_emails` receives a *"AWS Notification - Subscription Confirmation"* email from `no-reply@sns.amazonaws.com`.
2. Each recipient must click **"Confirm subscription"** in that email.
3. Until confirmed the subscription shows as **PendingConfirmation** in the AWS console — no alerts are delivered to that address.

Confirmation links expire after **3 days**. If a link expires, re-trigger by
deleting and re-adding the address from `alert_emails` and re-applying.

## Adding or removing subscribers

Edit `alert_emails` in the relevant environment's tfvars (or the variable
default in `infrastructure/dev/variables.tf`) and re-apply:

```hcl
# infrastructure/dev/terraform.tfvars (example)
alert_emails = [
  "ops@example.com",
  "oncall@example.com",
]
```

Each address is a separate `aws_sns_topic_subscription` resource keyed by
email string. Adding an address creates one new subscription without touching
existing ones. Removing an address destroys only that subscription.

## Silencing alarms in ephemeral stacks

Set `alarms_enabled = false` to create the alarms in `OK` state with actions
disabled. This prevents dev teardown/standup cycles from flooding inboxes while
keeping the alarm resources in place for inspection.

```hcl
alarms_enabled = false
```

## Alarm inventory and thresholds

All thresholds are variables with sane defaults. Override via tfvars.

### ALB

| Alarm | Metric | Default threshold | Period |
|---|---|---|---|
| `*-alb-5xx-high` | `HTTPCode_Target_5XX_Count` | > 10 per 5 min | 1 × 5 min |
| `*-alb-p95-latency-high` | `TargetResponseTime` p95 | > 2 s | 2 × 5 min |

### ECS — API service

| Alarm | Metric | Default threshold | Period |
|---|---|---|---|
| `*-ecs-api-cpu-high` | `CPUUtilization` | > 80 % | 2 × 5 min |
| `*-ecs-api-memory-high` | `MemoryUtilization` | > 80 % | 2 × 5 min |
| `*-ecs-api-tasks-low` | `RunningTaskCount` | < 1 | 2 × 1 min |

### ECS — Celery worker service

| Alarm | Metric | Default threshold | Period |
|---|---|---|---|
| `*-ecs-worker-cpu-high` | `CPUUtilization` | > 80 % | 2 × 5 min |
| `*-ecs-worker-memory-high` | `MemoryUtilization` | > 80 % | 2 × 5 min |
| `*-ecs-worker-tasks-low` | `RunningTaskCount` | < 1 | 2 × 1 min |

### RDS / Aurora

| Alarm | Metric | Default threshold | Period |
|---|---|---|---|
| `*-rds-cpu-high` | `CPUUtilization` | > 80 % | 2 × 5 min |
| `*-rds-memory-low` | `FreeableMemory` | < 256 MiB | 2 × 5 min |
| `*-rds-connections-high` | `DatabaseConnections` | > 100 | 2 × 5 min |

### ElastiCache / Redis

| Alarm | Metric | Default threshold | Period |
|---|---|---|---|
| `*-cache-cpu-high` | `CPUUtilization` | > 80 % | 2 × 5 min |
| `*-cache-evictions-high` | `Evictions` | > 100 per 5 min | 1 × 5 min |
| `*-cache-connections-high` | `CurrConnections` | > 500 | 2 × 5 min |

## Viewing alarm history

In the AWS console: **CloudWatch → Alarms → All alarms** → filter by name
prefix (e.g. `forge-dev-`). Each alarm's **History** tab shows state
transitions with timestamps. CLI equivalent:

```bash
aws cloudwatch describe-alarm-history \
  --alarm-name forge-dev-alb-5xx-high \
  --history-item-type StateUpdate
```

## Future fan-out

The SNS topic ARN is exported as `module.alerts.sns_topic_arn`. Additional
subscribers (PagerDuty HTTPS endpoint, Slack Lambda, etc.) can be added as
`aws_sns_topic_subscription` resources in a follow-up PR without modifying
this module.
