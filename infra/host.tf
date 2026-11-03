# The engineering host runs the kleos.cli workers next to a local Docker Engine, which every native
# lane (CAD, PCB, firmware, app), checker, simulation, slicer and MCP tool needs: the worker shares
# host directories with the containers it starts. It is started on demand (API wake or the external
# wait tick) and powers itself off after host_idle_minutes idle. Stopped, it costs only its volume.
# Replacing it is safe: durable state is in Aurora/S3 and the image cache reloads from S3.
data "aws_ssm_parameter" "al2023_x86" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  host_log_group      = "/kleos/${var.name}/host"
  tick_schedule_name  = "${var.name}-external-wait-tick"
  tick_schedule_arn   = "arn:${local.partition}:scheduler:${var.region}:${local.account_id}:schedule/default/${local.tick_schedule_name}"
  control_image_param = "${local.ssm_host_path}/control-image"
  # Same paths inside the worker containers and on the host, so Docker bind mounts resolve.
  host_env_static = merge(local.common_env, {
    AWS_REGION                                = var.region
    AWS_DEFAULT_REGION                        = var.region
    KLEOS_ARTIFACT_ROOT                       = "/srv/kleos/artifacts"
    KLEOS_NATIVE_CONTROL_ROOT                 = "/srv/kleos/native"
    KLEOS_NATIVE_DAEMON_CONTROL_ROOT          = "/srv/kleos/native"
    KLEOS_CHECKER_CONTROL_ROOT                = "/srv/kleos/checkers"
    KLEOS_SLICER_CONTROL_ROOT                 = "/srv/kleos/slicing"
    KLEOS_SIMULATION_CONTROL_ROOT             = "/srv/kleos/simulation"
    KLEOS_MCP_CONTROL_ROOT                    = "/srv/kleos/mcp"
    KLEOS_MCP_DAEMON_CONTROL_ROOT             = "/srv/kleos/mcp"
    KLEOS_DOCKER_SOCKET                       = "/var/run/docker.sock"
    KLEOS_NATIVE_DOCKER_DESKTOP_COMPATIBILITY = "false"
  })
}

resource "aws_cloudwatch_log_group" "host" {
  name              = local.host_log_group
  retention_in_days = var.log_retention_days
}

resource "aws_iam_role" "host" {
  name = "${var.name}-host"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
  }] })
}
resource "aws_iam_role_policy_attachment" "host_ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy" "host" {
  role = aws_iam_role.host.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"],
      Resource = ["${local.ssm_parameter_arn}${local.ssm_runtime_path}", "${local.ssm_parameter_arn}${local.ssm_runtime_path}/*",
    "${local.ssm_parameter_arn}${local.control_image_param}"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.artifacts.arn}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.toolchains.arn}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.toolchains.arn },
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"],
    Resource = aws_ecr_repository.control.arn },
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.host.arn}:*" },
    # Arm or disarm the external-wait tick just before powering off.
    { Effect = "Allow", Action = ["scheduler:GetSchedule", "scheduler:UpdateSchedule"], Resource = local.tick_schedule_arn },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = aws_iam_role.tick.arn,
    Condition = { StringEquals = { "iam:PassedToService" = "scheduler.amazonaws.com" } } }
  ] })
}
resource "aws_iam_instance_profile" "host" {
  name = "${var.name}-host"
  role = aws_iam_role.host.name
}

resource "aws_ssm_parameter" "control_image" {
  name  = local.control_image_param
  type  = "String"
  value = var.control_image == "" ? "unset" : var.control_image
  lifecycle {
    ignore_changes = [value] # Releases update the worker image together with the API Lambda.
  }
}

resource "aws_instance" "host" {
  ami                                  = data.aws_ssm_parameter.al2023_x86.value
  instance_type                        = var.host_instance_type
  subnet_id                            = aws_subnet.public[0].id
  associate_public_ip_address          = true
  vpc_security_group_ids               = [aws_security_group.host.id]
  iam_instance_profile                 = aws_iam_instance_profile.host.name
  instance_initiated_shutdown_behavior = "stop"
  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # Hop limit 1: only host-network processes (the workers) reach instance credentials; bridged
    # containers cannot. Native jobs additionally run with no network at all.
    http_put_response_hop_limit = 1
  }
  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.host_volume_gib
    encrypted             = true
    delete_on_termination = true
  }
  user_data = templatefile("${path.module}/host-user-data.sh.tftpl", {
    name                = var.name
    region              = var.region
    ssm_runtime_path    = local.ssm_runtime_path
    control_image_param = local.control_image_param
    toolchain_bucket    = aws_s3_bucket.toolchains.id
    log_group           = local.host_log_group
    worker_commands     = join(" ", var.host_worker_commands)
    idle_minutes        = var.host_idle_minutes
    max_session_minutes = var.host_max_session_minutes
    tick_schedule       = local.tick_schedule_name
    tick_role_arn       = aws_iam_role.tick.arn
    tick_expression     = "rate(${var.external_wait_tick_minutes} minutes)"
    host_env            = join("\n", [for key in sort(keys(local.host_env_static)) : "${key}=${local.host_env_static[key]}"])
  })
  # The boot script only installs units on first boot, so a changed script needs a fresh host.
  user_data_replace_on_change = true
  depends_on                  = [aws_iam_role_policy.host, aws_iam_role_policy_attachment.host_ssm, aws_cloudwatch_log_group.host]
  tags                        = { Name = "${var.name}-host" }
  lifecycle {
    ignore_changes = [ami] # A new AMI should not replace the host on every plan.
  }
}

# --- External-wait tick: EventBridge Scheduler starts the host directly (no Lambda). Created
# disabled; the host enables it when it powers off with waits outstanding and disables it otherwise.
resource "aws_iam_role" "tick" {
  name = "${var.name}-external-wait-tick"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "scheduler.amazonaws.com" }, Action = "sts:AssumeRole",
    Condition = { StringEquals = { "aws:SourceAccount" = local.account_id } }
  }] })
}
resource "aws_iam_role_policy" "tick" {
  role = aws_iam_role.tick.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ec2:StartInstances"], Resource = aws_instance.host.arn }
  ] })
}
resource "aws_scheduler_schedule" "tick" {
  name                = local.tick_schedule_name
  schedule_expression = "rate(${var.external_wait_tick_minutes} minutes)"
  state               = "DISABLED"
  flexible_time_window { mode = "OFF" }
  target {
    arn      = "arn:aws:scheduler:::aws-sdk:ec2:startInstances"
    role_arn = aws_iam_role.tick.arn
    input    = jsonencode({ InstanceIds = [aws_instance.host.id] })
  }
  lifecycle {
    ignore_changes = [state] # Owned by the host at runtime.
  }
}
