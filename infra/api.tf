locals {
  ssm_parameter_arn = "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter"
  # Structural, non-secret settings. Credentials and tool installations (KLEOS_DATABASE_URL,
  # KLEOS_SESSION_SECRET, KLEOS_META_API_KEY, KLEOS_NATIVE_TOOLS, ...) are SSM parameters under
  # local.ssm_runtime_path, written by an operator and never stored in Terraform state.
  common_env = {
    KLEOS_ENVIRONMENT               = "production"
    KLEOS_FRONTEND_ORIGIN           = "https://${var.app_hostname}"
    KLEOS_API_ORIGIN                = "https://${var.api_hostname}"
    KLEOS_PREVIEW_ORIGIN            = "https://${var.preview_hostname}"
    KLEOS_COMPANION_PREVIEW_ENABLED = tostring(var.companion_preview_enabled)
    KLEOS_ARTIFACT_BUCKET           = aws_s3_bucket.artifacts.id
    KLEOS_TOOLCHAIN_BUCKET          = aws_s3_bucket.toolchains.id
    KLEOS_SSM_RUNTIME_PATH          = local.ssm_runtime_path
  }
  # Lambda can write only /tmp. Qualification evidence is fetched to the path the host also uses.
  lambda_env = merge(local.common_env, {
    KLEOS_WAKE_INSTANCE_ID        = aws_instance.host.id
    KLEOS_ARTIFACT_ROOT           = "/tmp/kleos/artifacts"
    KLEOS_NATIVE_CONTROL_ROOT     = "/tmp/kleos/native"
    KLEOS_CHECKER_CONTROL_ROOT    = "/tmp/kleos/checkers"
    KLEOS_SLICER_CONTROL_ROOT     = "/tmp/kleos/slicing"
    KLEOS_SIMULATION_CONTROL_ROOT = "/tmp/kleos/simulation"
    KLEOS_MCP_CONTROL_ROOT        = "/tmp/kleos/mcp"
    # Lambda Web Adapter proxies invocations to uvicorn and streams responses (event streams).
    AWS_LWA_PORT                 = "8000"
    AWS_LWA_READINESS_CHECK_PATH = "/api/v1/ready"
    AWS_LWA_INVOKE_MODE          = "response_stream"
  })
  serve_command   = ["serve", "--host", "127.0.0.1", "--port", "8000"]
  preview_enabled = local.api_enabled && var.companion_preview_enabled
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(["api", "preview"])
  name              = "/aws/lambda/${var.name}-${each.key}"
  retention_in_days = var.log_retention_days
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# --- API ---
resource "aws_iam_role" "api" {
  name               = "${var.name}-api"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}
resource "aws_iam_role_policy" "api" {
  role = aws_iam_role.api.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda["api"].arn}:*" },
    { Effect = "Allow", Action = ["ssm:GetParametersByPath", "ssm:GetParameters", "ssm:GetParameter"],
    Resource = ["${local.ssm_parameter_arn}${local.ssm_runtime_path}", "${local.ssm_parameter_arn}${local.ssm_runtime_path}/*"] },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.artifacts.arn}/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.artifacts.arn },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.toolchains.arn}/qualification/*" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.toolchains.arn,
    Condition = { StringLike = { "s3:prefix" = ["qualification/*"] } } },
    # Wake the engineering host when work is queued. StartInstances is a no-op on a running host.
    { Effect = "Allow", Action = ["ec2:StartInstances"], Resource = aws_instance.host.arn },
    { Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*" }
  ] })
}
resource "aws_lambda_function" "api" {
  count                          = local.api_enabled ? 1 : 0
  function_name                  = "${var.name}-api"
  role                           = aws_iam_role.api.arn
  package_type                   = "Image"
  image_uri                      = var.control_image
  architectures                  = ["x86_64"]
  memory_size                    = var.api_memory_mb
  timeout                        = 900 # Event streams end here; the workbench reconnects by cursor.
  reserved_concurrent_executions = var.api_reserved_concurrency
  ephemeral_storage { size = 1024 }
  image_config { command = local.serve_command }
  environment { variables = local.lambda_env }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda["api"].name
  }
  depends_on = [aws_iam_role_policy.api, aws_ecr_repository_policy.control]
  lifecycle {
    ignore_changes = [image_uri] # Releases (see README) own image revisions after the first apply.
  }
}

# --- Companion preview (opt-in, separate origin, no provider credentials) ---
resource "aws_iam_role" "preview" {
  name               = "${var.name}-preview"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
}
resource "aws_iam_role_policy" "preview" {
  role = aws_iam_role.preview.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.lambda["preview"].arn}:*" },
    # Named parameters only: provider credentials and the setup token stay unreadable, matching compose.yaml.
    { Effect = "Allow", Action = ["ssm:GetParameters", "ssm:GetParameter"],
    Resource = [for key in var.preview_runtime_parameters : "${local.ssm_parameter_arn}${local.ssm_runtime_path}/${key}"] },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.artifacts.arn}/*" }
  ] })
}
resource "aws_lambda_function" "preview" {
  count                          = local.preview_enabled ? 1 : 0
  function_name                  = "${var.name}-preview"
  role                           = aws_iam_role.preview.arn
  package_type                   = "Image"
  image_uri                      = var.control_image
  architectures                  = ["x86_64"]
  memory_size                    = 512
  timeout                        = 60
  reserved_concurrent_executions = 4
  image_config { command = ["preview-serve", "--host", "127.0.0.1", "--port", "8000"] }
  environment {
    variables = merge(local.lambda_env, { KLEOS_SSM_PARAMETERS = join(",", var.preview_runtime_parameters) })
  }
  logging_config {
    log_format = "Text"
    log_group  = aws_cloudwatch_log_group.lambda["preview"].name
  }
  depends_on = [aws_iam_role_policy.preview, aws_ecr_repository_policy.control]
  lifecycle { ignore_changes = [image_uri] }
}

# --- Function URLs (fronted by CloudFront in edge.tf) ---
locals {
  lambda_urls = merge(
    local.api_enabled ? { api = aws_lambda_function.api[0].function_name } : {},
    local.preview_enabled ? { preview = aws_lambda_function.preview[0].function_name } : {}
  )
}
resource "aws_lambda_function_url" "app" {
  for_each           = local.lambda_urls
  function_name      = each.value
  authorization_type = "NONE" # Each process authenticates requests itself.
  invoke_mode        = "RESPONSE_STREAM"
}
# Public Function URLs need both permissions since AWS's October 2025 change.
resource "aws_lambda_permission" "url" {
  for_each               = local.lambda_urls
  statement_id           = "FunctionUrlPublic"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = each.value
  principal              = "*"
  function_url_auth_type = "NONE"
}
resource "aws_lambda_permission" "url_invoke" {
  for_each                 = local.lambda_urls
  statement_id             = "FunctionUrlInvoke"
  action                   = "lambda:InvokeFunction"
  function_name            = each.value
  principal                = "*"
  invoked_via_function_url = true
}
