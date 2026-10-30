variable "github_repository" {
  type    = string
  default = "manuu-r/kleos9"
}
variable "enable_github_deploy" {
  type        = bool
  default     = false
  description = "Enable release permissions only after the GitHub environment allows deployments from main alone."
}
variable "github_environment" {
  type    = string
  default = "production"
}
variable "github_oidc_provider_arn" {
  type        = string
  default     = ""
  description = "Existing GitHub OIDC provider ARN, if the account already has one."
}
resource "aws_iam_openid_connect_provider" "github" {
  count          = var.github_oidc_provider_arn == "" ? 1 : 0
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}
resource "aws_iam_role" "github_deploy" {
  name = "${var.name}-github-deploy"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Federated = var.github_oidc_provider_arn != "" ? var.github_oidc_provider_arn : aws_iam_openid_connect_provider.github[0].arn }
      Action    = "sts:AssumeRoleWithWebIdentity"
      Condition = { StringEquals = {
        "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        "token.actions.githubusercontent.com:sub" = "repo:${var.github_repository}:environment:${var.github_environment}"
      } }
    }]
  })
}
resource "aws_iam_role_policy" "github_deploy" {
  count = var.enable_github_deploy ? 1 : 0
  role  = aws_iam_role.github_deploy.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
      { Effect = "Allow", Action = ["ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:DescribeImages"], Resource = aws_ecr_repository.images["control"].arn },
      { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = [for service in aws_ecs_service.control : service.id] },
      { Effect = "Allow", Action = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"], Resource = "*" },
      { Effect = "Allow", Action = ["ecs:TagResource"], Resource = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*", Condition = { StringEquals = { "ecs:CreateAction" = "RunTask" } } },
      { Effect = "Allow", Action = ["ecs:RunTask"], Resource = [for task in aws_ecs_task_definition.control : "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${task.family}:*"], Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } } },
      { Effect = "Allow", Action = ["ecs:DescribeTasks", "ecs:StopTask"], Resource = "arn:${data.aws_partition.current.partition}:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${aws_ecs_cluster.main.name}/*", Condition = { ArnEquals = { "ecs:cluster" = aws_ecs_cluster.main.arn } } },
      { Effect = "Allow", Action = ["iam:PassRole"], Resource = [aws_iam_role.control.arn, aws_iam_role.execution.arn], Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
      { Effect = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.frontend.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.frontend.arn}/*" },
      { Effect = "Allow", Action = ["cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"], Resource = aws_cloudfront_distribution.frontend.arn }
    ]
  })
}
output "github_deploy_role_arn" { value = aws_iam_role.github_deploy.arn }
