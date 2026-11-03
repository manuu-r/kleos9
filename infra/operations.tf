resource "aws_sqs_queue" "completion_dlq" {
  name                      = "${var.name}-completion-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_sqs_queue" "completions" {
  name                       = "${var.name}-completions"
  visibility_timeout_seconds = 120
  message_retention_seconds  = 1209600
  sqs_managed_sse_enabled    = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.completion_dlq.arn
    maxReceiveCount     = 5
  })
}
resource "aws_cloudwatch_event_rule" "task_stopped" {
  name = "${var.name}-task-stopped"
  event_pattern = jsonencode({
    source = ["aws.ecs"], "detail-type" = ["ECS Task State Change"]
    detail = { clusterArn = concat([aws_ecs_cluster.main.arn], var.recovery_cluster_arns), lastStatus = ["STOPPED"] }
  })
}
resource "aws_cloudwatch_event_target" "completion" {
  rule = aws_cloudwatch_event_rule.task_stopped.name
  arn  = aws_sqs_queue.completions.arn
}
resource "aws_sqs_queue_policy" "completions" {
  queue_url = aws_sqs_queue.completions.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "events.amazonaws.com" }
    Action    = "sqs:SendMessage", Resource = aws_sqs_queue.completions.arn
    Condition = { ArnEquals = { "aws:SourceArn" = aws_cloudwatch_event_rule.task_stopped.arn } }
  }] })
}
resource "aws_sns_topic" "alerts" { name = "${var.name}-alerts" }
resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
resource "aws_cloudwatch_metric_alarm" "completion_age" {
  alarm_name          = "${var.name}-completion-age"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateAgeOfOldestMessage"
  dimensions          = { QueueName = aws_sqs_queue.completions.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 180
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "completion_dead_letters" {
  alarm_name          = "${var.name}-completion-dead-letters"
  namespace           = "AWS/SQS"
  metric_name         = "ApproximateNumberOfMessagesVisible"
  dimensions          = { QueueName = aws_sqs_queue.completion_dlq.name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.name}-api-errors"
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  dimensions          = { LoadBalancer = aws_lb.api.arn_suffix }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 5
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "database_storage" {
  alarm_name          = "${var.name}-database-storage"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.id }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 2
  threshold           = 5368709120
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "worker_heartbeat" {
  for_each            = var.activate_services ? toset(["engineering", "connectors"]) : toset([])
  alarm_name          = "${var.name}-${each.key}-heartbeat"
  namespace           = "Kleos9"
  metric_name         = "WorkerHeartbeat"
  dimensions          = { WorkerKind = each.key, Environment = var.environment }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "LessThanThreshold"
  treat_missing_data  = "breaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "daily_allowance" {
  alarm_name          = "${var.name}-daily-allowance-exhausted"
  namespace           = "Kleos9"
  metric_name         = "DailyAllowanceRemainingUsd"
  dimensions          = { Environment = var.environment }
  statistic           = "Minimum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "LessThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "gate_violation" {
  alarm_name          = "${var.name}-gate-violation"
  namespace           = "Kleos9"
  metric_name         = "GateViolation"
  dimensions          = { Environment = var.environment }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "reconciliation_failure" {
  alarm_name          = "${var.name}-reconciliation-failure"
  namespace           = "Kleos9"
  metric_name         = "ReconciliationFailure"
  dimensions          = { Environment = var.environment }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
resource "aws_cloudwatch_metric_alarm" "engineering_queue_age" {
  alarm_name          = "${var.name}-engineering-queue-age"
  namespace           = "Kleos9"
  metric_name         = "QueueAgeSeconds"
  dimensions          = { Environment = var.environment }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  threshold           = 300
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}
locals {
  otel_config = yamlencode({
    receivers  = { otlp = { protocols = { http = { endpoint = "127.0.0.1:4318" } } } }
    processors = { batch = {}, memory_limiter = { check_interval = "1s", limit_mib = 128 } }
    exporters  = { awsxray = { region = var.region } }
    service = { pipelines = { traces = {
      receivers = ["otlp"], processors = ["memory_limiter", "batch"], exporters = ["awsxray"]
    } } }
  })
  otel_container = {
    name        = "telemetry", image = "otel/opentelemetry-collector-contrib:0.137.0"
    essential   = false, memoryReservation = 192
    command     = ["--config=env:OTEL_CONFIG"]
    environment = [{ name = "OTEL_CONFIG", value = local.otel_config }]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = "telemetry"
    }) }
  }
}
resource "aws_cloudwatch_dashboard" "overview" {
  dashboard_name = var.name
  dashboard_body = jsonencode({ widgets = [
    { type = "metric", x = 0, y = 0, width = 12, height = 6, properties = {
      title = "API latency and errors", region = var.region, period = 60
      metrics = [
        ["AWS/ApplicationELB", "TargetResponseTime", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "p95" }],
        ["AWS/ApplicationELB", "HTTPCode_Target_5XX_Count", "LoadBalancer", aws_lb.api.arn_suffix, { stat = "Sum", yAxis = "right" }]
      ]
    } },
    { type = "metric", x = 12, y = 0, width = 12, height = 6, properties = {
      title = "Completion backlog", region = var.region, period = 60
      metrics = [
        ["AWS/SQS", "ApproximateAgeOfOldestMessage", "QueueName", aws_sqs_queue.completions.name],
        ["AWS/SQS", "ApproximateNumberOfMessagesVisible", "QueueName", aws_sqs_queue.completions.name]
      ]
    } },
    { type = "metric", x = 0, y = 6, width = 24, height = 6, properties = {
      title = "Independent worker capacity", region = var.region, period = 60
      metrics = [for name in ["api", "engineering", "connectors"] :
      ["AWS/ECS", "CPUUtilization", "ClusterName", aws_ecs_cluster.main.name, "ServiceName", name]]
    } },
    { type = "metric", x = 0, y = 12, width = 12, height = 6, properties = {
      title = "Engineering queue and index backlog", region = var.region, period = 60
      metrics = [
        ["Kleos9", "QueueAgeSeconds", "Environment", var.environment, { stat = "Maximum" }],
        ["Kleos9", "IndexBacklog", "Environment", var.environment, { stat = "Maximum", yAxis = "right" }],
        ["Kleos9", "OutboxPending", "Environment", var.environment, { stat = "Maximum", yAxis = "right" }]
      ]
    } },
    { type = "metric", x = 12, y = 12, width = 12, height = 6, properties = {
      title = "Daily configured allowance (USD)", region = var.region, period = 60
      metrics = [
        ["Kleos9", "DailyAllowanceRemainingUsd", "Environment", var.environment, { stat = "Minimum" }],
        ["Kleos9", "DailyAllowanceUsedUsd", "Environment", var.environment, { stat = "Maximum" }]
      ]
    } },
    { type = "metric", x = 0, y = 18, width = 12, height = 6, properties = {
      title = "Current validation coverage and repairs", region = var.region, period = 60
      metrics = [
        ["Kleos9", "UnknownChecks", "Environment", var.environment, { stat = "Maximum" }],
        ["Kleos9", "FailedChecks", "Environment", var.environment, { stat = "Maximum" }],
        ["Kleos9", "RepairAttempts", "Environment", var.environment, { stat = "Maximum" }],
        ["Kleos9", "ValidationPassPercent", "Environment", var.environment, { stat = "Minimum", yAxis = "right" }]
      ]
    } },
    { type = "metric", x = 12, y = 18, width = 12, height = 6, properties = {
      title = "Safety, reconciliation and worker heartbeat", region = var.region, period = 60
      metrics = [
        ["Kleos9", "GateViolation", "Environment", var.environment, { stat = "Sum" }],
        ["Kleos9", "ReconciliationFailure", "Environment", var.environment, { stat = "Sum" }],
        ["Kleos9", "WorkerHeartbeat", "Environment", var.environment, "WorkerKind", "engineering", { stat = "Sum", yAxis = "right" }],
        ["Kleos9", "WorkerHeartbeat", "Environment", var.environment, "WorkerKind", "connectors", { stat = "Sum", yAxis = "right" }]
      ]
    } },
    { type = "metric", x = 0, y = 24, width = 12, height = 6, properties = {
      title = "Native queue and container startup", region = var.region, period = 60
      metrics = concat(
        [for lane in ["mechanics", "electronics", "firmware", "application", "review"] :
        ["Kleos9", "NativeQueueAgeSeconds", "Lane", lane, { stat = "Maximum" }]],
        [["Kleos9", "NativeImagePullSeconds", "Backend", "ecs", { stat = "Maximum", yAxis = "right" }]]
      )
    } },
    { type = "metric", x = 12, y = 24, width = 12, height = 6, properties = {
      title = "Native peak memory and CPU time", region = var.region, period = 60
      metrics = concat(
        [for lane in ["mechanics", "electronics", "firmware", "application", "review"] :
        ["Kleos9", "NativeMemoryPeakBytes", "Lane", lane, { stat = "Maximum" }]],
        [for lane in ["mechanics", "electronics", "firmware", "application", "review"] :
        ["Kleos9", "NativeCpuUsageSeconds", "Lane", lane, { stat = "Maximum", yAxis = "right" }]]
      )
    } }
  ] })
}
