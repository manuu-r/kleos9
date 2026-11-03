resource "aws_vpc_security_group_ingress_rule" "internal_workers" {
  security_group_id            = aws_security_group.internal_lb.id
  referenced_security_group_id = aws_security_group.trusted.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
