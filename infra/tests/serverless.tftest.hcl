# All providers are mocked: these runs create no AWS resources.
mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws", dns_suffix = "amazonaws.com" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-east-1a", "us-east-1b"] } }
  mock_data "aws_ssm_parameter" { defaults = { value = "ami-0123456789abcdef0" } }
  mock_data "aws_iam_policy_document" { defaults = { json = "{}" } }
  mock_resource "aws_lambda_function_url" { defaults = { function_url = "https://abc123.lambda-url.us-east-1.on.aws/" } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock" } }
  mock_resource "aws_cloudwatch_log_group" { defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:mock" } }
  mock_resource "aws_s3_bucket" {
    defaults = { id = "mock-bucket", arn = "arn:aws:s3:::mock-bucket", bucket_regional_domain_name = "mock-bucket.s3.us-east-1.amazonaws.com" }
  }
  mock_resource "aws_ecr_repository" { defaults = { arn = "arn:aws:ecr:us-east-1:123456789012:repository/mock" } }
  mock_resource "aws_cloudfront_distribution" { defaults = { arn = "arn:aws:cloudfront::123456789012:distribution/MOCK" } }
  mock_resource "aws_ssm_parameter" { defaults = { arn = "arn:aws:ssm:us-east-1:123456789012:parameter/mock" } }
  mock_resource "aws_instance" {
    defaults = { id = "i-0123456789abcdef0", arn = "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0" }
  }
}
mock_provider "aws" {
  alias = "global"
  mock_resource "aws_acm_certificate" { defaults = { arn = "arn:aws:acm:us-east-1:123456789012:certificate/mock" } }
  mock_resource "aws_acm_certificate_validation" { defaults = { certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/mock" } }
}

variables {
  name             = "kleos9-test"
  app_hostname     = "app.example.test"
  api_hostname     = "api.example.test"
  preview_hostname = "preview.example.test"
  control_image    = "123456789012.dkr.ecr.us-east-1.amazonaws.com/kleos9-test/control@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
}

run "idle_cost_and_isolation_defaults" {
  command = apply
  assert {
    condition = (
      aws_rds_cluster.main.serverlessv2_scaling_configuration[0].min_capacity == 0 &&
      aws_rds_cluster.main.serverlessv2_scaling_configuration[0].seconds_until_auto_pause == 300 &&
      aws_rds_cluster.main.deletion_protection && !aws_rds_cluster.main.skip_final_snapshot &&
      aws_rds_cluster_instance.main.instance_class == "db.serverless"
    )
    error_message = "The database must pause to 0 ACU and keep deletion protection and a final snapshot."
  }
  assert {
    condition     = one([for p in aws_rds_cluster_parameter_group.main.parameter : p.value if p.name == "rds.force_ssl"]) == "1"
    error_message = "A publicly reachable database must require TLS."
  }
  assert {
    condition = (
      aws_instance.host.instance_initiated_shutdown_behavior == "stop" &&
      aws_instance.host.metadata_options[0].http_tokens == "required" &&
      aws_instance.host.metadata_options[0].http_put_response_hop_limit == 1 &&
      aws_instance.host.root_block_device[0].encrypted
    )
    error_message = "The host must stop (not terminate) on idle power-off and keep IMDSv2 with hop limit 1."
  }
  assert {
    condition     = aws_scheduler_schedule.tick.state == "DISABLED"
    error_message = "The external-wait tick starts disabled; only the host arms it."
  }
  assert {
    condition = (
      length(aws_lambda_function.api) == 1 && length(aws_lambda_function.preview) == 0 &&
      aws_lambda_function_url.app["api"].invoke_mode == "RESPONSE_STREAM" &&
      length(aws_cloudfront_distribution.lambda) == 1
    )
    error_message = "The API streams through one CloudFront distribution; previews stay off by default."
  }
  assert {
    condition = length(setintersection(keys(aws_lambda_function.api[0].environment[0].variables),
    ["KLEOS_DATABASE_URL", "KLEOS_SESSION_SECRET", "KLEOS_SETUP_TOKEN", "KLEOS_META_API_KEY", "KLEOS_MODAL_API_KEY"])) == 0
    error_message = "Credentials come from SSM at runtime, never from Terraform-managed environment."
  }
  assert {
    condition = (
      aws_lambda_function.api[0].environment[0].variables["KLEOS_ENVIRONMENT"] == "production" &&
      aws_lambda_function.api[0].environment[0].variables["KLEOS_PREVIEW_ORIGIN"] != aws_lambda_function.api[0].environment[0].variables["KLEOS_FRONTEND_ORIGIN"] &&
      aws_lambda_function.api[0].environment[0].variables["KLEOS_WAKE_INSTANCE_ID"] == aws_instance.host.id
    )
    error_message = "The API runs production settings, a separate preview origin and can wake the host."
  }
}

run "no_api_without_an_image" {
  command = apply
  variables { control_image = "" }
  assert {
    condition     = length(aws_lambda_function.api) == 0 && length(aws_cloudfront_distribution.lambda) == 0
    error_message = "Without a control image only storage, database and host are provisioned."
  }
}

run "preview_is_separate_and_credential_free" {
  command = apply
  variables { companion_preview_enabled = true }
  assert {
    condition = (
      length(aws_lambda_function.preview) == 1 && length(aws_cloudfront_distribution.lambda) == 2 &&
      aws_lambda_function.preview[0].role == aws_iam_role.preview.arn &&
      aws_lambda_function.preview[0].environment[0].variables["KLEOS_SSM_PARAMETERS"] == "KLEOS_DATABASE_URL,KLEOS_SESSION_SECRET"
    )
    error_message = "The preview has its own role, origin and an explicit parameter allowlist."
  }
}

run "preview_cannot_read_provider_credentials" {
  command = plan
  variables { preview_runtime_parameters = ["KLEOS_DATABASE_URL", "KLEOS_META_API_KEY"] }
  expect_failures = [var.preview_runtime_parameters]
}

run "legacy_name_is_rejected" {
  command = plan
  variables { name = "kleos9-production" }
  expect_failures = [var.name]
}
