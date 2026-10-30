output "api_url" { value = "https://${local.api_hostname}" }
output "artifact_bucket" { value = aws_s3_bucket.artifacts.id }
output "runtime_secret_arn" { value = aws_secretsmanager_secret.runtime.arn }
output "database_admin_secret_arn" { value = aws_db_instance.main.master_user_secret[0].secret_arn }
output "database_host" { value = aws_db_instance.main.address }
output "cluster" { value = aws_ecs_cluster.main.arn }
output "private_subnets" { value = aws_subnet.private[*].id }
output "trusted_security_group" { value = aws_security_group.trusted.id }
output "ecr_repositories" { value = { for name, repo in aws_ecr_repository.images : name => repo.repository_url } }
output "control_task_definitions" { value = { for name, task in aws_ecs_task_definition.control : name => task.arn } }
output "cognito_pool_id" { value = aws_cognito_user_pool.main.id }
output "cognito_client_id" { value = aws_cognito_user_pool_client.main.id }
output "cognito_domain" { value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${var.region}.amazoncognito.com" }
output "qdrant_instance_id" { value = aws_instance.qdrant.id }
output "dashboard" { value = "https://${var.region}.console.aws.amazon.com/cloudwatch/home?region=${var.region}#dashboards:name=${aws_cloudwatch_dashboard.overview.dashboard_name}" }
