provider "aws" {
  alias  = "global"
  region = "us-east-1"
}
locals {
  frontend_hostname = trimsuffix(trimprefix(var.frontend_url, "https://"), "/")
}
resource "aws_acm_certificate" "frontend" {
  provider                  = aws.global
  domain_name               = local.frontend_hostname
  subject_alternative_names = [local.api_hostname, local.jobs_hostname]
  validation_method         = "DNS"
  lifecycle { create_before_destroy = true }
}
resource "aws_route53_record" "frontend_validation" {
  for_each = var.public_zone_id == "" ? {} : { for option in aws_acm_certificate.frontend.domain_validation_options : option.domain_name => option }
  zone_id  = var.public_zone_id
  name     = each.value.resource_record_name
  type     = each.value.resource_record_type
  records  = [each.value.resource_record_value]
  ttl      = 60
}
resource "aws_acm_certificate_validation" "frontend" {
  provider                = aws.global
  certificate_arn         = aws_acm_certificate.frontend.arn
  validation_record_fqdns = [for option in aws_acm_certificate.frontend.domain_validation_options : option.resource_record_name]
  depends_on              = [aws_route53_record.frontend_validation]
}
resource "aws_s3_bucket" "frontend" {
  bucket_prefix = "${var.name}-frontend-"
}
resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${var.name}-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_cloudfront_distribution" "frontend" {
  enabled             = true
  default_root_object = "index.html"
  aliases             = [local.frontend_hostname]
  price_class         = "PriceClass_100"
  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }
  default_cache_behavior {
    target_origin_id       = "frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    min_ttl                = 0
    default_ttl            = 300
    max_ttl                = 86400
    forwarded_values {
      query_string = false
      cookies { forward = "none" }
    }
  }
  restrictions {
    geo_restriction { restriction_type = "none" }
  }
  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.frontend.certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
}
resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Principal = { Service = "cloudfront.amazonaws.com" },
    Action    = "s3:GetObject", Resource = "${aws_s3_bucket.frontend.arn}/*",
    Condition = { StringEquals = { "AWS:SourceArn" = aws_cloudfront_distribution.frontend.arn } }
  }] })
}
resource "aws_route53_record" "frontend" {
  count   = var.public_zone_id == "" ? 0 : 1
  zone_id = var.public_zone_id
  name    = local.frontend_hostname
  type    = "A"
  alias {
    name                   = aws_cloudfront_distribution.frontend.domain_name
    zone_id                = aws_cloudfront_distribution.frontend.hosted_zone_id
    evaluate_target_health = false
  }
}
output "frontend_bucket" { value = aws_s3_bucket.frontend.id }
output "frontend_distribution_id" { value = aws_cloudfront_distribution.frontend.id }
output "frontend_url" { value = var.frontend_url }

output "frontend_dns_target" { value = aws_cloudfront_distribution.frontend.domain_name }
output "frontend_certificate_validation_records" {
  value = [for option in aws_acm_certificate.frontend.domain_validation_options : {
    name = option.resource_record_name, type = option.resource_record_type, value = option.resource_record_value
  }]
}
