resource "aws_vpc" "main" {
  cidr_block           = "10.42.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = { Name = var.name }
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
  vpc_id            = aws_vpc.main.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 20)
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
}
resource "aws_route_table_association" "private" {
  count          = 2
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
# Native tasks have no NAT or internet route.
resource "aws_route_table" "native" { vpc_id = aws_vpc.main.id }
resource "aws_route_table_association" "native" {
  count          = 2
  subnet_id      = aws_subnet.native[count.index].id
  route_table_id = aws_route_table.native.id
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
  vpc_id = aws_vpc.main.id
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
}
resource "aws_security_group" "endpoints" {
  name   = "${var.name}-endpoints"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    security_groups = concat([aws_security_group.native.id, aws_security_group.api.id, aws_security_group.trusted.id], aws_security_group.simulation[*].id)
  }
}
resource "aws_security_group_rule" "native_endpoints" {
  type                     = "egress"
  from_port                = 443
  to_port                  = 443
  protocol                 = "tcp"
  security_group_id        = aws_security_group.native.id
  source_security_group_id = aws_security_group.endpoints.id
}
resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(["ecr.api", "ecr.dkr", "logs"])
  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
}
resource "aws_vpc_endpoint" "s3" {
  vpc_id          = aws_vpc.main.id
  service_name    = "com.amazonaws.${var.region}.s3"
  route_table_ids = [aws_route_table.native.id]
  # Native code cannot use this endpoint to exfiltrate to an arbitrary S3 bucket.
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Principal = "*", Action = ["s3:GetObject"],
    Resource = "arn:${data.aws_partition.current.partition}:s3:::prod-${var.region}-starport-layer-bucket/*" },
    { Effect = "Allow", Principal = "*", Action = ["s3:*"],
    Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"] }
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
  depends_on = [aws_vpc_endpoint.s3]
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
}
resource "aws_vpc_security_group_egress_rule" "internal_api" {
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}
