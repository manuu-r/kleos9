resource "aws_lb" "api" {
  name               = "${var.name}-api"
  internal           = false
  load_balancer_type = "application"
  subnets            = aws_subnet.public[*].id
  security_groups    = [aws_security_group.edge.id]
  idle_timeout       = 120
}
resource "aws_lb" "internal" {
  name               = "${var.name}-jobs"
  internal           = true
  load_balancer_type = "application"
  subnets            = aws_subnet.native[*].id
  security_groups    = [aws_security_group.internal_lb.id]
  idle_timeout       = 120
}
resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id
  health_check { path = "/api/v1/health" }
}
resource "aws_lb_target_group" "internal" {
  name        = "${var.name}-jobs"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.main.id
  health_check { path = "/api/v1/health" }
}
resource "aws_lb_listener" "api" {
  load_balancer_arn = aws_lb.api.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
resource "aws_lb_listener_rule" "deny_internal" {
  listener_arn = aws_lb_listener.api.arn
  priority     = 1
  condition {
    path_pattern { values = ["/api/v1/internal/*"] }
  }
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
      message_body = "Not found"
    }
  }
}
resource "aws_lb_listener" "internal" {
  load_balancer_arn = aws_lb.internal.arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      status_code  = "404"
    }
  }
}
resource "aws_lb_listener_rule" "native_api" {
  listener_arn = aws_lb_listener.internal.arn
  priority     = 1
  condition {
    path_pattern { values = ["/api/v1/internal/*"] }
  }
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.internal.arn
  }
}
resource "aws_route53_record" "api" {
  count   = var.public_zone_id == "" ? 0 : 1
  zone_id = var.public_zone_id
  name    = local.api_hostname
  type    = "A"
  alias {
    name                   = aws_lb.api.dns_name
    zone_id                = aws_lb.api.zone_id
    evaluate_target_health = true
  }
}
resource "aws_route53_record" "api_private" {
  zone_id = aws_route53_zone.private.zone_id
  name    = local.api_hostname
  type    = "A"
  alias {
    name                   = aws_lb.api.dns_name
    zone_id                = aws_lb.api.zone_id
    evaluate_target_health = true
  }
}
resource "aws_route53_record" "jobs" {
  zone_id = aws_route53_zone.private.zone_id
  name    = local.jobs_hostname
  type    = "A"
  alias {
    name                   = aws_lb.internal.dns_name
    zone_id                = aws_lb.internal.zone_id
    evaluate_target_health = true
  }
}

output "api_dns_target" { value = aws_lb.api.dns_name }
