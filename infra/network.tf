resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = var.name }
}
# DNS Firewall applies to a whole VPC, so generated jobs cannot share the
# trusted workers' VPC while keeping provider DNS available to those workers.
resource "aws_vpc" "native" {
  cidr_block           = "10.43.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = "${var.name}-native" }
}
resource "aws_internet_gateway" "main" { vpc_id = aws_vpc.main.id }
resource "aws_subnet" "public" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
}
resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
}
resource "aws_subnet" "native" {
  count             = 2
  vpc_id            = aws_vpc.native.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.native.cidr_block, 8, count.index)
}
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
}
resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}
resource "aws_eip" "nat" { domain = "vpc" }
resource "aws_nat_gateway" "main" {
  subnet_id     = aws_subnet.public[0].id
  allocation_id = aws_eip.nat.id
  depends_on    = [aws_internet_gateway.main]
}
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  # Keep all routes inline for this table; mixing standalone aws_route resources
  # with inline routes would cause Terraform to remove each other's routes.
  dynamic "route" {
    for_each = aws_subnet.native
    content {
      cidr_block                = route.value.cidr_block
      vpc_peering_connection_id = aws_vpc_peering_connection.native_callback.id
    }
  }
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
# Native tasks have no NAT, internet gateway, public IP or default route.
resource "aws_route_table" "native" { vpc_id = aws_vpc.native.id }
resource "aws_route_table_association" "native" {
  count          = 2
  subnet_id      = aws_subnet.native[count.index].id
  route_table_id = aws_route_table.native.id
}
resource "aws_vpc_peering_connection" "native_callback" {
  vpc_id      = aws_vpc.native.id
  peer_vpc_id = aws_vpc.main.id
  auto_accept = true
  tags        = { Name = "${var.name}-native-callback" }
}
resource "aws_route" "native_callback" {
  count                     = 2
  route_table_id            = aws_route_table.native.id
  destination_cidr_block    = aws_subnet.private[count.index].cidr_block
  vpc_peering_connection_id = aws_vpc_peering_connection.native_callback.id
}
resource "aws_security_group" "edge" {
  name   = "${var.name}-edge"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }
  egress {
    from_port       = 8000
    to_port         = 8000
    protocol        = "tcp"
    security_groups = concat([aws_security_group.api.id], aws_security_group.simulation[*].id)
  }
}
resource "aws_security_group" "api" {
  name   = "${var.name}-api"
  vpc_id = aws_vpc.main.id
}
resource "aws_security_group" "trusted" {
  name   = "${var.name}-trusted"
  vpc_id = aws_vpc.main.id
}
resource "aws_security_group" "native" {
  name   = "${var.name}-native"
  vpc_id = aws_vpc.native.id
}
resource "aws_security_group" "internal_lb" {
  name   = "${var.name}-internal-lb"
  vpc_id = aws_vpc.main.id
}
resource "aws_security_group_rule" "api_public" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.api.id
  source_security_group_id = aws_security_group.edge.id
}
resource "aws_security_group_rule" "api_internal" {
  type                     = "ingress"
  from_port                = 8000
  to_port                  = 8000
  protocol                 = "tcp"
  security_group_id        = aws_security_group.api.id
  source_security_group_id = aws_security_group.internal_lb.id
}
resource "aws_security_group_rule" "native_callback" {
  type                     = "egress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.native.id
  source_security_group_id = aws_security_group.internal_lb.id
  depends_on               = [aws_vpc_peering_connection.native_callback]
}
resource "aws_security_group" "endpoints" {
  name   = "${var.name}-endpoints"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = concat([aws_security_group.api.id, aws_security_group.trusted.id], aws_security_group.simulation[*].id)
  }
}
resource "aws_security_group" "native_endpoints" {
  name   = "${var.name}-native-endpoints"
  vpc_id = aws_vpc.native.id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = [aws_security_group.native.id]
  }
}
resource "aws_security_group_rule" "native_endpoints" {
  type                     = "egress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.native.id
  source_security_group_id = aws_security_group.native_endpoints.id
}

locals {
  # Image configuration is operator-controlled. Derive exact repository ARNs,
  # never a repository/account wildcard, including approved alternative tools.
  ecr_image_pattern = "^([0-9]{12})[.]dkr[.]ecr[.]([a-z0-9-]+)[.]${replace(data.aws_partition.current.dns_suffix, ".", "[.]")}/([a-z0-9][a-z0-9._/-]*)@sha256:[a-f0-9]{64}$"
  configured_ecr_repositories = flatten([
    for image in distinct(compact(concat(values(var.native_images), [var.control_image, var.simulation_image, var.conductor_image]))) : [
      for match in regexall(local.ecr_image_pattern, image) :
      "arn:${data.aws_partition.current.partition}:ecr:${match[1]}:${match[0]}:repository/${match[2]}"
    ]
  ])
  native_image_matches = { for key, image in var.native_images : key => regexall(local.ecr_image_pattern, image) }
  native_image_locations_approved = alltrue([
    for matches in values(local.native_image_matches) : try(
      length(matches) == 1 && matches[0][0] == data.aws_caller_identity.current.account_id && matches[0][1] == var.region,
      false
    )
  ])
  native_repository_arns = distinct(concat(
    [for lane, repository in aws_ecr_repository.images : repository.arn if lane != "control"],
    flatten([for matches in values(local.native_image_matches) : [
      for match in matches : "arn:${data.aws_partition.current.partition}:ecr:${match[1]}:${match[0]}:repository/${match[2]}"
    ]])
  ))
  trusted_repository_arns = distinct(concat(
    [for repository in aws_ecr_repository.images : repository.arn],
    [aws_ecr_repository.simulation.arn], local.configured_ecr_repositories
  ))
  native_endpoint_principals = concat([aws_iam_role.native_execution.arn], var.recovery_execution_role_arns)
  trusted_endpoint_principals = concat(
    [aws_iam_role.execution.arn, aws_iam_role.native_execution.arn], var.recovery_execution_role_arns
  )
  endpoint_scopes = {
    native  = { principals = local.native_endpoint_principals, repositories = local.native_repository_arns, streams = "${aws_cloudwatch_log_group.app.arn}:log-stream:native-*" }
    trusted = { principals = local.trusted_endpoint_principals, repositories = local.trusted_repository_arns, streams = "${aws_cloudwatch_log_group.app.arn}:log-stream:*" }
  }
  ecr_endpoint_policies = { for scope, limits in local.endpoint_scopes : scope => jsonencode({
    Version = "2012-10-17", Statement = [
      { Sid = "ExecutionRoleToken", Effect = "Allow", Principal = "*", Action = ["ecr:GetAuthorizationToken"], Resource = "*",
      Condition = { ArnEquals = { "aws:PrincipalArn" = limits.principals } } },
      { Sid    = "ApprovedRepositoryPull", Effect = "Allow", Principal = "*",
        Action = ["ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"], Resource = limits.repositories,
      Condition = { ArnEquals = { "aws:PrincipalArn" = limits.principals } } }
    ]
  }) }
  logs_endpoint_policies = { for scope, limits in local.endpoint_scopes : scope => jsonencode({
    Version = "2012-10-17", Statement = [
      { Sid    = "ExecutionRoleLogDelivery", Effect = "Allow", Principal = "*",
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = limits.streams,
      Condition = { ArnEquals = { "aws:PrincipalArn" = limits.principals } } }
    ]
  }) }
}
resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs"])
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  policy              = each.key == "logs" ? local.logs_endpoint_policies.trusted : local.ecr_endpoint_policies.trusted
}
resource "aws_vpc_endpoint" "native_interface" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs"])
  vpc_id              = aws_vpc.native.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.native[*].id
  security_group_ids  = [aws_security_group.native_endpoints.id]
  policy              = each.key == "logs" ? local.logs_endpoint_policies.native : local.ecr_endpoint_policies.native
  lifecycle {
    precondition {
      condition     = local.native_image_locations_approved
      error_message = "Native images must be immutable private ECR images in this account and region; mirror other images into an approved local repository."
    }
  }
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.native.id
  service_name    = "com.amazonaws.${var.region}.s3"
  route_table_ids = [aws_route_table.native.id]
  # ECR's signed layer downloads use its service bucket identity, not the task
  # execution role. Permit only GET on that exact bucket; artifacts use callbacks.
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = "*", Action = ["s3:GetObject"],
    Resource = "arn:${data.aws_partition.current.partition}:s3:::prod-${var.region}-starport-layer-bucket/*" }
  ] })
}
resource "aws_vpc_endpoint" "s3_trusted" {
  vpc_id          = aws_vpc.main.id
  service_name    = "com.amazonaws.${var.region}.s3"
  route_table_ids = [aws_route_table.private.id]
  # Trusted hosts need Amazon Linux packages; native jobs retain the stricter endpoint.
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = "*", Action = ["s3:GetObject"], Resource = [
      "arn:${data.aws_partition.current.partition}:s3:::prod-${var.region}-starport-layer-bucket/*",
      "arn:${data.aws_partition.current.partition}:s3:::al2023-repos-${var.region}-*/*",
      "arn:${data.aws_partition.current.partition}:s3:::docker-images-prod/*"
    ] },
    { Effect = "Allow", Principal = "*", Action = ["s3:*"],
    Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"] }
  ] })
}
resource "aws_security_group_rule" "native_s3_image_layers" {
  type              = "egress"
  from_port         = 443
  to_port           = 443
  protocol          = "tcp"
  security_group_id = aws_security_group.native.id
  prefix_list_ids   = [aws_vpc_endpoint.s3.prefix_list_id]
}
resource "aws_route53_zone" "private" {
  name = var.base_domain
  vpc { vpc_id = aws_vpc.main.id }
  vpc { vpc_id = aws_vpc.native.id }
}

locals {
  native_dns_domains = distinct(concat([
    local.jobs_hostname,
    aws_lb.internal.dns_name,
    "api.ecr.${var.region}.${data.aws_partition.current.dns_suffix}",
    "${data.aws_caller_identity.current.account_id}.dkr.ecr.${var.region}.${data.aws_partition.current.dns_suffix}",
    "logs.${var.region}.${data.aws_partition.current.dns_suffix}",
    "prod-${var.region}-starport-layer-bucket.s3.${var.region}.${data.aws_partition.current.dns_suffix}",
    "prod-${var.region}-starport-layer-bucket.s3-${var.region}.${data.aws_partition.current.dns_suffix}",
    "prod-${var.region}-starport-layer-bucket.s3.${data.aws_partition.current.dns_suffix}"
    ], flatten([for endpoint in aws_vpc_endpoint.native_interface : [
      # ECR DKR returns wildcard DNS entries. Narrow each to this exact registry;
      # never admit arbitrary accounts or attacker-controlled subdomains.
      for entry in endpoint.dns_entry : replace(entry.dns_name, "*.", "${data.aws_caller_identity.current.account_id}.")
  ]])))
}
resource "aws_route53_resolver_firewall_domain_list" "native_allow" {
  name    = "${var.name}-native-allow"
  domains = local.native_dns_domains
}
resource "aws_route53_resolver_firewall_domain_list" "native_deny" {
  name    = "${var.name}-native-deny"
  domains = ["*"]
}
resource "aws_route53_resolver_firewall_rule_group" "native" { name = "${var.name}-native" }
resource "aws_route53_resolver_firewall_rule" "native_allow" {
  name                    = "approved-bootstrap-and-callback"
  action                  = "ALLOW"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.native_allow.id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.native.id
  # Only these exact names are accepted as original queries. Their authorities
  # are AWS or this private zone; trust their managed CNAME chains, not arbitrary
  # original names or subdomains. Endpoint policies still limit actual access.
  firewall_domain_redirection_action = "TRUST_REDIRECTION_DOMAIN"
  priority                           = 100
}
resource "aws_route53_resolver_firewall_rule" "native_deny" {
  name                    = "deny-other-domains"
  action                  = "BLOCK"
  block_response          = "NXDOMAIN"
  firewall_domain_list_id = aws_route53_resolver_firewall_domain_list.native_deny.id
  firewall_rule_group_id  = aws_route53_resolver_firewall_rule_group.native.id
  priority                = 200
}
resource "aws_route53_resolver_firewall_config" "native" {
  resource_id        = aws_vpc.native.id
  firewall_fail_open = "DISABLED"
}
resource "aws_route53_resolver_firewall_rule_group_association" "native" {
  name                   = "${var.name}-native"
  firewall_rule_group_id = aws_route53_resolver_firewall_rule_group.native.id
  vpc_id                 = aws_vpc.native.id
  priority               = 100
  mutation_protection    = "DISABLED"
  depends_on = [
    aws_route53_resolver_firewall_rule.native_allow,
    aws_route53_resolver_firewall_rule.native_deny,
    aws_route53_resolver_firewall_config.native
  ]
}

resource "aws_vpc_security_group_egress_rule" "trusted_outbound" {
  for_each          = { api = aws_security_group.api.id, trusted = aws_security_group.trusted.id }
  security_group_id = each.value
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
resource "aws_vpc_security_group_ingress_rule" "internal_native" {
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.native.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  depends_on                   = [aws_vpc_peering_connection.native_callback]
}
resource "aws_vpc_security_group_egress_rule" "internal_api" {
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
