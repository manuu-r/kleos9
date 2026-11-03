resource "aws_iam_role" "bootstrap" {
  name               = "${var.name}-bootstrap"
  assume_role_policy = local.task_trust
}
resource "aws_iam_role_policy" "bootstrap" {
  role = aws_iam_role.bootstrap.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"],
    Resource = [aws_db_instance.main.master_user_secret[0].secret_arn, aws_secretsmanager_secret.runtime.arn] },
    { Effect = "Allow", Action = ["secretsmanager:PutSecretValue"], Resource = aws_secretsmanager_secret.runtime.arn },
    { Effect = "Allow", Action = ["kms:Decrypt", "kms:GenerateDataKey"], Resource = aws_kms_key.data.arn }
  ] })
}
resource "aws_ecs_task_definition" "bootstrap" {
  family                   = "${var.name}-bootstrap"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.native_execution.arn
  task_role_arn            = aws_iam_role.bootstrap.arn
  container_definitions = jsonencode([{
    name    = "bootstrap", image = var.control_image, essential = true, user = "1000"
    command = ["python", "/opt/kleos/bootstrap_database.py"]
    environment = [
      { name = "AWS_REGION", value = var.region },
      { name = "DATABASE_HOST", value = aws_db_instance.main.address },
      { name = "ADMIN_SECRET_ARN", value = aws_db_instance.main.master_user_secret[0].secret_arn },
      { name = "RUNTIME_SECRET_ARN", value = aws_secretsmanager_secret.runtime.arn }
    ]
    logConfiguration = { logDriver = "awslogs", options = merge(local.log_options, {
      "awslogs-stream-prefix" = "bootstrap"
    }) }
  }])
}
output "bootstrap_task_definition" { value = aws_ecs_task_definition.bootstrap.arn }
