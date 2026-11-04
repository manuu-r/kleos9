mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws", dns_suffix = "amazonaws.com" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-east-1a", "us-east-1b"] } }
  mock_resource "aws_vpc_endpoint" {
    defaults = { dns_entry = [{ dns_name = "*.vpce-test.dkr.ecr.us-east-1.vpce.amazonaws.com", hosted_zone_id = "ZTEST" }] }
  }
  mock_resource "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:us-east-1:123456789012:repository/approved" } }
  mock_resource "aws_lb" { defaults = { dns_name = "internal-callback.us-east-1.elb.amazonaws.com" } }
}
mock_provider "aws" { alias = "global" }
mock_provider "random" {}
override_resource {
  target = aws_vpc.main
  values = { id = "vpc-trusted" }
}
override_resource {
  target = aws_vpc.native
  values = { id = "vpc-native" }
}
override_resource {
  target = aws_iam_role.execution
  values = { arn = "arn:aws:iam::123456789012:role/execution" }
}
override_resource {
  target = aws_iam_role.native_execution
  values = { arn = "arn:aws:iam::123456789012:role/native-execution" }
}
override_resource {
  target = aws_cloudwatch_log_group.app
  values = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/kleos/test" }
}
variables {
  region          = "us-east-1"
  name            = "network-test"
  base_domain     = "example.test"
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
  frontend_url    = "https://app.example.test"
  public_zone_id  = ""
  control_image   = "123456789012.dkr.ecr.us-east-1.amazonaws.com/control@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  native_images = {
    mechanics = "123456789012.dkr.ecr.us-east-1.amazonaws.com/custom/cad@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}

run "native_network_boundaries" {
  command = apply # Mock providers only: no AWS requests or remote state.
  plan_options {
    target = [
      aws_route53_resolver_firewall_rule_group_association.native,
      aws_route53_zone.private, aws_vpc_endpoint.interface, aws_vpc_endpoint.s3,
      aws_route_table.private, aws_route.native_callback,
      aws_security_group_rule.native_callback, aws_vpc_security_group_ingress_rule.internal_native
    ]
  }
  assert {
    condition = (
      aws_subnet.native[0].vpc_id == "vpc-native" && aws_subnet.private[0].vpc_id == "vpc-trusted" &&
      aws_security_group.native.vpc_id == "vpc-native" && aws_route_table.native.vpc_id == "vpc-native" &&
      toset(aws_lb.internal.subnets) == toset(aws_subnet.private[*].id)
    )
    error_message = "Native jobs must be in their own VPC and the callback ALB must remain in trusted private subnets."
  }
  assert {
    condition = (
      aws_route53_resolver_firewall_rule_group_association.native.vpc_id == "vpc-native" &&
      aws_route53_resolver_firewall_config.native.firewall_fail_open == "DISABLED" &&
      aws_route53_resolver_firewall_rule.native_deny.block_response == "NXDOMAIN" &&
      aws_route53_resolver_firewall_rule.native_allow.priority < aws_route53_resolver_firewall_rule.native_deny.priority &&
      aws_route53_resolver_firewall_rule.native_allow.firewall_domain_redirection_action == "TRUST_REDIRECTION_DOMAIN" &&
      alltrue([for name in aws_route53_resolver_firewall_domain_list.native_allow.domains : !strcontains(name, "*")]) &&
      contains(aws_route53_resolver_firewall_domain_list.native_allow.domains, "jobs.example.test") &&
      contains(aws_route53_resolver_firewall_domain_list.native_allow.domains, "123456789012.vpce-test.dkr.ecr.us-east-1.vpce.amazonaws.com") &&
      !contains(aws_route53_resolver_firewall_domain_list.native_allow.domains, "generativelanguage.googleapis.com")
    )
    error_message = "Native DNS must fail closed, permit exact original names only, and deny provider/arbitrary names."
  }
  assert {
    condition = (
      aws_vpc_endpoint.native_interface["ecr.api"].policy == aws_vpc_endpoint.native_interface["ecr.dkr"].policy &&
      jsondecode(aws_vpc_endpoint.native_interface["ecr.api"].policy).Statement[0].Resource == "*" &&
      toset(jsondecode(aws_vpc_endpoint.native_interface["ecr.api"].policy).Statement[0].Action) == toset(["ecr:GetAuthorizationToken"]) &&
      contains(jsondecode(aws_vpc_endpoint.native_interface["ecr.api"].policy).Statement[1].Resource, "arn:aws:ecr:us-east-1:123456789012:repository/custom/cad") &&
      alltrue([for arn in jsondecode(aws_vpc_endpoint.native_interface["ecr.api"].policy).Statement[1].Resource : !strcontains(arn, "*")]) &&
      toset(jsondecode(aws_vpc_endpoint.native_interface["ecr.api"].policy).Statement[1].Condition.ArnEquals["aws:PrincipalArn"]) == toset(["arn:aws:iam::123456789012:role/native-execution"])
    )
    error_message = "Private ECR must allow only execution-role authentication and exact approved repository pulls."
  }
  assert {
    condition = (
      toset(jsondecode(aws_vpc_endpoint.native_interface["logs"].policy).Statement[0].Action) == toset(["logs:CreateLogStream", "logs:PutLogEvents"]) &&
      jsondecode(aws_vpc_endpoint.native_interface["logs"].policy).Statement[0].Resource == "arn:aws:logs:us-east-1:123456789012:log-group:/kleos/test:log-stream:native-*" &&
      toset(jsondecode(aws_vpc_endpoint.native_interface["logs"].policy).Statement[0].Condition.ArnEquals["aws:PrincipalArn"]) == toset(["arn:aws:iam::123456789012:role/native-execution"]) &&
      length(jsondecode(aws_vpc_endpoint.s3.policy).Statement) == 1 &&
      toset(jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Action) == toset(["s3:GetObject"]) &&
      jsondecode(aws_vpc_endpoint.s3.policy).Statement[0].Resource == "arn:aws:s3:::prod-us-east-1-starport-layer-bucket/*"
    )
    error_message = "Native log delivery and S3 access must not permit log reads, other log groups, artifacts or arbitrary bucket writes."
  }
  assert {
    condition = (
      length(aws_route_table.private.route) == 3 &&
      length([for route in aws_route_table.private.route : route if route.cidr_block == "0.0.0.0/0" && route.nat_gateway_id != ""]) == 1 &&
      alltrue([for route in aws_route.native_callback : contains(aws_subnet.private[*].cidr_block, route.destination_cidr_block)]) &&
      toset([for association in aws_route53_zone.private.vpc : association.vpc_id]) == toset(["vpc-native", "vpc-trusted"])
    )
    error_message = "Peering must route only callbacks while retaining trusted NAT and private DNS associations."
  }
}

run "reject_other_registry_native_image" {
  command = plan
  variables {
    native_images = { mechanics = "999999999999.dkr.ecr.us-east-1.amazonaws.com/cad@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" }
  }
  plan_options { target = [aws_vpc_endpoint.native_interface["ecr.api"]] }
  expect_failures = [aws_vpc_endpoint.native_interface["ecr.api"]]
}
