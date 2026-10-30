variable "simulation_image" {
  type        = string
  default     = ""
  description = "Simulation service image pinned to an ECR sha256 digest; empty disables hosted simulation."
  validation {
    condition     = var.simulation_image == "" || can(regex("@sha256:[a-f0-9]{64}$", var.simulation_image))
    error_message = "Use an immutable simulation image digest."
  }
}
locals { simulation_enabled = var.simulation_image != "" }
resource "aws_ecr_repository" "simulation" {
  name                 = "${var.name}/simulation"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_security_group" "simulation" {
  count  = local.simulation_enabled ? 1 : 0
  name   = "${var.name}-simulation"
  vpc_id = aws_vpc.main.id

}
resource "aws_vpc_security_group_ingress_rule" "simulation_from_edge" {
  count                        = local.simulation_enabled ? 1 : 0
  security_group_id            = aws_security_group.simulation[0].id
  referenced_security_group_id = aws_security_group.edge.id
  from_port                    = 8000
  to_port                      = 8000
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_egress_rule" "simulation_to_endpoints" {
  count                        = local.simulation_enabled ? 1 : 0
  security_group_id            = aws_security_group.simulation[0].id
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}
resource "aws_vpc_security_group_egress_rule" "simulation_s3_layers" {
  count             = local.simulation_enabled ? 1 : 0
  security_group_id = aws_security_group.simulation[0].id
  prefix_list_id    = aws_vpc_endpoint.s3_trusted.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_lb_target_group" "simulation" {
  count                = local.simulation_enabled ? 1 : 0
  name                 = "${var.name}-sim"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15
  health_check { path = "/api/health" }
}
resource "aws_lb_listener_rule" "simulation" {
  count        = local.simulation_enabled ? 1 : 0
  listener_arn = aws_lb_listener.api.arn
  priority     = 10
  condition {
    path_pattern { values = ["/api/state", "/api/commands", "/api/demo/*", "/api/health"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.simulation[0].arn
  }
}
resource "aws_ecs_task_definition" "simulation" {
  count                    = local.simulation_enabled ? 1 : 0
  family                   = "${var.name}-simulation"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.native_execution.arn
  # No task role, runtime secrets, database access, or provider credentials.
  runtime_platform {
    cpu_architecture        = "X86_64"
    operating_system_family = "LINUX"
  }
  container_definitions = jsonencode([{
    name         = "simulation", image = var.simulation_image, essential = true,
    user         = "10001", readonlyRootFilesystem = true,
    portMappings = [{ containerPort = 8000, protocol = "tcp" }],
    environment  = [{ name = "KLEOS9_UI_ORIGINS", value = var.frontend_url }],
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = "simulation"
    }) }
  }])
}
resource "aws_ecs_service" "simulation" {
  count           = local.simulation_enabled ? 1 : 0
  name            = "simulation"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.simulation[0].arn
  desired_count   = 1
  launch_type     = "FARGATE"
  # The demo owns one in-memory state; never serve two divergent copies.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.simulation[0].id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.simulation[0].arn
    container_name   = "simulation"
    container_port   = 8000
  }
  depends_on = [aws_lb_listener_rule.simulation, aws_vpc_security_group_egress_rule.simulation_to_endpoints, aws_vpc_security_group_egress_rule.simulation_s3_layers, aws_vpc_security_group_ingress_rule.simulation_from_edge]
  lifecycle { ignore_changes = [task_definition] }
}
output "simulation_ecr_repository" { value = aws_ecr_repository.simulation.repository_url }
output "simulation_api_url" { value = local.simulation_enabled ? "https://${local.api_hostname}/api" : "" }
