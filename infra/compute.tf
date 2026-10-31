resource "aws_cloudwatch_log_group" "app" {
  name              = "/kleos/${var.name}"
  retention_in_days = 30
}
resource "aws_ecr_repository" "images" {
  for_each             = toset(["control", "mechanics", "electronics", "firmware", "application", "validate"])
  name                 = "${var.name}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_ecs_cluster" "main" {
  name = var.name
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
locals {
  task_trust = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }
  ] })
  log_options = {
    "awslogs-group"         = aws_cloudwatch_log_group.app.name
    "awslogs-region"        = var.region
    "awslogs-stream-prefix" = "service"
  }
}
resource "aws_iam_role" "execution" {
  name               = "${var.name}-execution"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "runtime_secrets" {
  role = aws_iam_role.execution.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = compact([
      aws_secretsmanager_secret.runtime.arn, aws_secretsmanager_secret.qdrant.arn,
      aws_secretsmanager_secret.setup.arn,
      var.conductor_license_secret_arn
    ]) },
    { Effect = "Allow", Action = ["kms:Decrypt"], Resource = aws_kms_key.data.arn }
  ] })
}
resource "aws_iam_role" "native_execution" {
  name               = "${var.name}-native-execution"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy_attachment" "native_execution" {
  role       = aws_iam_role.native_execution.name
  policy_arn = "arn:${data.aws_partition.current.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role" "control" {
  name               = "${var.name}-control"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy" "control" {
  role = aws_iam_role.control.id
  policy = jsonencode({ Version = "2012-10-17", Statement = concat([
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"],
    Resource = "${aws_s3_bucket.artifacts.arn}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions"], Resource = aws_s3_bucket.artifacts.arn },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.data.arn },
    { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask", "ecs:ListTasks"], Resource = "*",
    Condition = { ArnEquals = { "ecs:cluster" = concat([aws_ecs_cluster.main.arn], var.recovery_cluster_arns) } } },
    { Effect = "Allow", Action = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"],
    Resource = aws_sqs_queue.completions.arn },
    { Effect = "Allow", Action = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "cloudwatch:PutMetricData"],
    Resource = "*" }
    ], [for statement in [
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = concat([for task in aws_ecs_task_definition.native : task.arn], var.recovery_task_definition_arns) },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = concat([aws_iam_role.native_execution.arn], var.recovery_execution_role_arns),
      Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
      { Effect = "Allow", Action = ["ecs:TagResource"], Resource = "*",
      Condition = { StringEquals = { "ecs:CreateAction" = "RunTask" } } }
  ] : statement if length(var.native_images) + length(var.recovery_task_definition_arns) > 0]) })
}
locals {
  # One version-controlled source is loaded by Terraform and backend Settings.
  native_resource_defaults = jsondecode(file("${path.module}/../backend/kleos/native-resources.json"))
  native_resource_profiles = merge(local.native_resource_defaults, var.native_resource_overrides)
  native_task_resources = {
    for key in keys(var.native_images) : key => lookup(local.native_resource_profiles, key, local.native_resource_profiles._default)
  }
  # Deliberately qualify a bounded subset of Linux Fargate task sizes.
  fargate_memory_mib = {
    "256"   = [512, 1024, 2048]
    "512"   = range(1024, 4097, 1024)
    "1024"  = range(2048, 8193, 1024)
    "2048"  = range(4096, 16385, 1024)
    "4096"  = range(8192, 30721, 1024)
    "8192"  = range(16384, 61441, 4096)
    "16384" = range(32768, 122881, 8192)
  }
  native_resource_profiles_valid = alltrue([for profile in values(local.native_resource_profiles) : try(
    toset(keys(profile)) == toset(["cpuUnits", "memoryMiB", "tmpMiB", "pidsLimit", "ephemeralStorageGiB"]) &&
    alltrue([for value in values(profile) : value > 0 && floor(value) == value]) &&
    contains(lookup(local.fargate_memory_mib, tostring(profile.cpuUnits), []), profile.memoryMiB) &&
    profile.tmpMiB <= profile.memoryMiB && profile.pidsLimit >= 32 && profile.pidsLimit <= 1024 &&
    profile.ephemeralStorageGiB >= 21 && profile.ephemeralStorageGiB <= 200,
    false
  )])
}
resource "aws_ecs_task_definition" "native" {
  for_each                 = var.native_images
  family                   = contains(["mechanics", "electronics", "firmware", "application", "validate"], each.key) ? "${var.name}-native-${each.key}" : "${var.name}-tool-${replace(each.key, "/[^a-zA-Z0-9_-]/", "-")}-${substr(sha256(each.key), 0, 8)}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = tostring(local.native_task_resources[each.key].cpuUnits)
  memory                   = tostring(local.native_task_resources[each.key].memoryMiB)
  execution_role_arn       = aws_iam_role.native_execution.arn
  # No task_role_arn: generated code has no AWS identity.
  ephemeral_storage { size_in_gib = local.native_task_resources[each.key].ephemeralStorageGiB }
  lifecycle {
    precondition {
      condition     = local.native_resource_profiles_valid
      error_message = "Shared and overridden native resource profiles must use supported Fargate sizes and bounded scratch/process/storage values."
    }
  }
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name = "native", image = each.value, essential = true,
    user = "0", readonlyRootFilesystem = true,
    # Fargate only permits adding SYS_PTRACE; retain the default UID-switching capabilities.
    linuxParameters = { capabilities = { add = [], drop = ["NET_RAW", "SYS_CHROOT", "MKNOD", "SETFCAP", "AUDIT_WRITE", "SETPCAP", "NET_BIND_SERVICE"] } },
    # Fargate has no Docker PidsLimit/tmpfs setting. nproc and the generated UID's
    # supervisor limit bound processes; /tmp uses the task's ephemeral volume.
    ulimits     = [{ name = "nproc", softLimit = local.native_task_resources[each.key].pidsLimit, hardLimit = local.native_task_resources[each.key].pidsLimit }],
    environment = [], portMappings = [], volumesFrom = [], systemControls = [],
    mountPoints = [{ sourceVolume = "work", containerPath = "/tmp", readOnly = false }],
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = "native-${each.key}"
    }) }
  }])
  volume { name = "work" }
}

locals {
  runtime_env = {
    KLEOS_ENVIRONMENT                 = var.environment
    KLEOS_DEMO_MODE                   = "true"
    KLEOS_AUTH_MODE                   = "password"
    KLEOS_STORAGE_BACKEND             = "s3"
    KLEOS_ARTIFACT_BUCKET             = aws_s3_bucket.artifacts.id
    KLEOS_AWS_REGION                  = var.region
    KLEOS_FRONTEND_URL                = var.frontend_url
    KLEOS_CORS_ORIGINS                = jsonencode([var.frontend_url])
    KLEOS_QDRANT_URL                  = "http://qdrant.${var.base_domain}:6333"
    KLEOS_ECS_CLUSTER                 = aws_ecs_cluster.main.arn
    KLEOS_ECS_SUBNETS                 = jsonencode(aws_subnet.native[*].id)
    KLEOS_ECS_SECURITY_GROUPS         = jsonencode([aws_security_group.native.id])
    KLEOS_ECS_TASK_DEFINITIONS        = jsonencode({ for key, value in aws_ecs_task_definition.native : key => value.arn })
    KLEOS_NATIVE_RESOURCE_PROFILES    = jsonencode(local.native_resource_profiles)
    KLEOS_MAX_NATIVE_JOBS             = tostring(var.max_native_jobs)
    KLEOS_MAX_NATIVE_JOBS_PER_PROJECT = tostring(var.max_native_jobs_per_project)
    KLEOS_NATIVE_JOB_RESERVATION_USD  = tostring(var.native_job_reservation_usd)
    KLEOS_ENGINEERING_TOOLS           = jsonencode(var.engineering_tools)
    KLEOS_NATIVE_SESSIONS_ENABLED     = tostring(var.native_sessions_enabled)
    KLEOS_EXECUTION_BACKEND           = length(var.native_images) + length(var.recovery_task_definition_arns) > 0 ? "ecs" : "disabled"
    KLEOS_INTERNAL_BASE_URL           = "https://${local.jobs_hostname}"
    KLEOS_SQS_COMPLETION_QUEUE        = aws_sqs_queue.completions.url
    KLEOS_SPENDING_LIMITS_ENABLED     = tostring(var.spending_limits_enabled)
    KLEOS_PROJECT_BUDGET_USD          = tostring(var.project_budget_usd)
    KLEOS_DAILY_BUDGET_USD            = tostring(var.daily_budget_usd)
    KLEOS_CONDUCTOR_URL               = local.conductor_enabled ? "wss://${local.jobs_hostname}/" : ""
    KLEOS_OTLP_ENDPOINT               = "http://localhost:4318/v1/traces"
  }
  runtime_secret_keys = [
    "KLEOS_DATABASE_URL", "KLEOS_DBOS_DATABASE_URL", "KLEOS_SESSION_KEY",
    "KLEOS_META_API_KEY", "KLEOS_GEMINI_API_KEY", "KLEOS_CONDUCTOR_KEY",
    "KLEOS_MODAL_LLM_URL", "KLEOS_MODAL_LLM_KEY",
    "KLEOS_DIGIKEY_CLIENT_ID", "KLEOS_DIGIKEY_CLIENT_SECRET", "KLEOS_WEBHOOK_SECRETS",
    "MODAL_TOKEN_ID", "MODAL_TOKEN_SECRET"
  ]
  runtime_secrets = concat([
    for key in local.runtime_secret_keys :
    { name = key, valueFrom = "${aws_secretsmanager_secret.runtime.arn}:${key}::" }
  ], [{ name = "KLEOS_QDRANT_KEY", valueFrom = aws_secretsmanager_secret.qdrant.arn }])
}
resource "aws_ecs_task_definition" "control" {
  for_each                 = toset(["api", "engineering", "connectors"])
  family                   = "${var.name}-${each.key}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 2048
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.control.arn
  container_definitions = jsonencode([{
    name = "control", image = var.control_image, essential = true, user = "1000",
    command = (each.key == "api" ?
      ["uvicorn", "kleos.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"] :
    ["python", "-m", "kleos.worker", "--kind", each.key]),
    portMappings = each.key == "api" ? [{ containerPort = 8000, protocol = "tcp" }] : [],
    environment  = [for key, value in local.runtime_env : { name = key, value = value }],
    # Only the API serves the sign-in page, so only it receives the first-run setup token.
    secrets = concat(local.runtime_secrets, each.key == "api" ?
    [{ name = "KLEOS_SETUP_TOKEN", valueFrom = aws_secretsmanager_secret.setup.arn }] : []),
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = each.key
    }) }
  }, local.otel_container])
}
resource "aws_ecs_service" "control" {
  for_each                           = toset(["api", "engineering", "connectors"])
  name                               = each.key
  cluster                            = aws_ecs_cluster.main.id
  task_definition                    = aws_ecs_task_definition.control[each.key].arn
  desired_count                      = var.activate_services ? 1 : 0
  launch_type                        = "FARGATE"
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [each.key == "api" ? aws_security_group.api.id : aws_security_group.trusted.id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = each.key == "api" ? { public = aws_lb_target_group.api.arn, internal = aws_lb_target_group.internal.arn } : {}
    content {
      target_group_arn = load_balancer.value
      container_name   = "control"
      container_port   = 8000
    }
  }
  depends_on = [
    aws_lb_listener.api, aws_lb_listener.internal, aws_route53_record.jobs,
    aws_route53_resolver_firewall_rule_group_association.native,
    aws_route.native_callback, aws_route_table.private, aws_route_table_association.native,
    aws_security_group_rule.native_callback, aws_vpc_security_group_ingress_rule.internal_native,
    aws_security_group_rule.native_endpoints, aws_security_group_rule.native_s3_image_layers,
    aws_vpc_endpoint.s3
  ]
  lifecycle {
    # CI owns release revisions; provisioning must not roll a service back.
    ignore_changes = [task_definition]
  }
}
resource "aws_appautoscaling_target" "control" {
  for_each           = toset(["api", "engineering", "connectors"])
  min_capacity       = var.activate_services ? 1 : 0
  max_capacity       = 4
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.control[each.key].name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}
resource "aws_appautoscaling_policy" "cpu" {
  for_each           = toset(["api", "engineering", "connectors"])
  name               = "${var.name}-${each.key}-cpu"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.control[each.key].resource_id
  scalable_dimension = aws_appautoscaling_target.control[each.key].scalable_dimension
  service_namespace  = aws_appautoscaling_target.control[each.key].service_namespace
  target_tracking_scaling_policy_configuration {
    target_value = 65
    predefined_metric_specification { predefined_metric_type = "ECSServiceAverageCPUUtilization" }
    scale_out_cooldown = 60
    scale_in_cooldown  = 300
  }
}
