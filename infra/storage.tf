resource "aws_kms_key" "data" {
  description             = "Kleos9 artifacts, database and runtime configuration"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}
resource "aws_kms_alias" "data" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.data.key_id
}
resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.name}-artifacts-"
  force_destroy = false
}
resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.data.arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "transcripts-30-days"
    status = "Enabled"
    filter { prefix = "transcripts/" }
    expiration { days = 30 }
    noncurrent_version_expiration { noncurrent_days = 30 }
  }
  rule {
    id     = "incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_security_group" "database" {
  name   = "${var.name}-database"
  vpc_id = aws_vpc.main.id
  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id, aws_security_group.trusted.id]
  }
}
resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.private[*].id
}
resource "aws_db_instance" "main" {
  identifier                      = var.name
  engine                          = "postgres"
  engine_version                  = "17"
  instance_class                  = "db.t4g.medium"
  allocated_storage               = 30
  max_allocated_storage           = 150
  storage_type                    = "gp3"
  storage_encrypted               = true
  kms_key_id                      = aws_kms_key.data.arn
  db_name                         = "kleos"
  username                        = "kleos_admin"
  manage_master_user_password     = true
  master_user_secret_kms_key_id   = aws_kms_key.data.arn
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  multi_az                        = var.database_multi_az
  publicly_accessible             = false
  backup_retention_period         = 7
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-final"
  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
}
resource "aws_secretsmanager_secret" "runtime" {
  name_prefix             = "${var.name}/runtime-"
  kms_key_id              = aws_kms_key.data.arn
  recovery_window_in_days = 30
  description             = "JSON runtime configuration. Populate via bootstrap and provider setup; values not stored in Terraform."
}
# One-time token for creating the first owner from the sign-in page. It stops working
# as soon as any account exists. Read it in the Secrets Manager console.
resource "aws_secretsmanager_secret" "setup" {
  name_prefix = "${var.name}/setup-token-"
  kms_key_id  = aws_kms_key.data.arn
  description = "Paste into the Kleos9 first-run setup form to create the first owner."
}
resource "random_password" "setup" {
  length  = 32
  special = false
}
resource "aws_secretsmanager_secret_version" "setup" {
  secret_id     = aws_secretsmanager_secret.setup.id
  secret_string = random_password.setup.result
}
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}
