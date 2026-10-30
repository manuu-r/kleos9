variable "conductor_oauth_issuer" {
  type        = string
  default     = ""
  description = "Operator SSO issuer for licensed self-hosted Conductor."
}
variable "conductor_oauth_audience" {
  type    = string
  default = ""
}
variable "activate_conductor" {
  type        = bool
  default     = false
  description = "Start licensed Conductor for registration before application services are activated."
}
resource "aws_vpc_security_group_ingress_rule" "internal_workers" {
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.trusted.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
resource "aws_vpc_security_group_ingress_rule" "conductor" {
  count                        = local.conductor_enabled ? 1 : 0
  security_group_id            = aws_security_group.trusted.id
  referenced_security_group_id = aws_security_group.internal_lb.id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_vpc_security_group_egress_rule" "conductor" {
  count                        = local.conductor_enabled ? 1 : 0
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.trusted.id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_lb_target_group" "conductor" {
  count       = local.conductor_enabled ? 1 : 0
  name        = "${var.name}-conductor"
  port        = 8090
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id
  health_check {
    path    = "/"
    matcher = "200-499"
  }
}
resource "aws_lb_listener_rule" "conductor" {
  count        = local.conductor_enabled ? 1 : 0
  listener_arn = aws_lb_listener.internal.arn
  priority     = 100
  condition {
    path_pattern { values = ["/*"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.conductor[0].arn
  }
}
resource "aws_ecs_task_definition" "conductor" {
  count                    = local.conductor_enabled ? 1 : 0
  family                   = "${var.name}-conductor"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 512
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  lifecycle {
    precondition {
      condition     = var.conductor_oauth_issuer != "" && var.conductor_oauth_audience != "" && can(regex("@sha256:[a-f0-9]{64}$", var.conductor_image))
      error_message = "Conductor needs a pinned image digest and operator SSO issuer/audience."
    }
  }
  container_definitions = jsonencode([{
    name         = "conductor", image = var.conductor_image, essential = true
    portMappings = [{ containerPort = 8090 }]
    environment = [
      { name = "DBOS_OAUTH_ENABLED", value = "true" },
      { name = "DBOS_OAUTH_ISSUER", value = var.conductor_oauth_issuer },
      { name = "DBOS_OAUTH_AUDIENCE", value = var.conductor_oauth_audience }
    ]
    secrets = [
      { name = "DBOS__CONDUCTOR_DB_URL", valueFrom = "${aws_secretsmanager_secret.runtime.arn}:CONDUCTOR_DATABASE_URL::" },
      { name = "DBOS_CONDUCTOR_LICENSE_KEY", valueFrom = var.conductor_license_secret_arn }
    ]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = "conductor"
    }) }
  }])
}
resource "aws_ecs_service" "conductor" {
  count           = local.conductor_enabled ? 1 : 0
  name            = "conductor"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.conductor[0].arn
  desired_count   = var.activate_conductor ? 1 : 0
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.trusted.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.conductor[0].arn
    container_name   = "conductor"
    container_port   = 8090
  }
  depends_on = [aws_lb_listener_rule.conductor]
}
