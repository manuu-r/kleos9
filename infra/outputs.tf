output "certificate_validation_records" {
  description = "Add as DNS-only CNAMEs in Cloudflare; apply waits for validation. Keep them for renewals."
  value = [for option in aws_acm_certificate.edge.domain_validation_options : {
    name = option.resource_record_name, type = option.resource_record_type, value = option.resource_record_value
  }]
}
output "dns_targets" {
  description = "DNS-only CNAME targets for each public hostname."
  value = merge(
    { (var.app_hostname) = aws_cloudfront_distribution.frontend.domain_name },
    { for key, distribution in aws_cloudfront_distribution.lambda : local.edge_hostnames[key] => distribution.domain_name }
  )
}
output "frontend_bucket" { value = aws_s3_bucket.frontend.id }
output "frontend_distribution_id" { value = aws_cloudfront_distribution.frontend.id }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "toolchain_bucket" { value = aws_s3_bucket.toolchains.id }
output "control_repository" { value = aws_ecr_repository.control.repository_url }
output "control_image_parameter" { value = aws_ssm_parameter.control_image.name }
output "ssm_runtime_path" { value = local.ssm_runtime_path }
output "host_instance_id" { value = aws_instance.host.id }
output "database_endpoint" { value = aws_rds_cluster.main.endpoint }
output "database_admin_secret_arn" { value = try(aws_rds_cluster.main.master_user_secret[0].secret_arn, "") }
output "api_function_url" { value = local.api_enabled ? aws_lambda_function_url.app["api"].function_url : "" }
