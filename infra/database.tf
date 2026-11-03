# Aurora Serverless v2 pauses at 0 ACU after database_auto_pause_seconds without connections, so an
# idle stack pays only for storage and the instance's public IPv4. The first connection after a pause
# waits roughly 15 seconds while it resumes. Open workbench tabs poll events and keep it awake.
resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = aws_subnet.public[*].id
}
resource "aws_rds_cluster_parameter_group" "main" {
  name   = "${var.name}-pg${split(".", var.database_engine_version)[0]}"
  family = "aurora-postgresql${split(".", var.database_engine_version)[0]}"
  parameter {
    name  = "rds.force_ssl"
    value = "1"
  }
}
resource "aws_rds_cluster" "main" {
  cluster_identifier              = var.name
  engine                          = "aurora-postgresql"
  engine_mode                     = "provisioned"
  engine_version                  = var.database_engine_version
  database_name                   = "kleos"
  master_username                 = "kleos_admin"
  manage_master_user_password     = true
  storage_encrypted               = true
  db_subnet_group_name            = aws_db_subnet_group.main.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.main.name
  backup_retention_period         = 7
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${var.name}-final"
  serverlessv2_scaling_configuration {
    min_capacity             = 0
    max_capacity             = var.database_max_acu
    seconds_until_auto_pause = var.database_auto_pause_seconds
  }
}
resource "aws_rds_cluster_instance" "main" {
  identifier                   = "${var.name}-1"
  cluster_identifier           = aws_rds_cluster.main.id
  instance_class               = "db.serverless"
  engine                       = aws_rds_cluster.main.engine
  engine_version               = aws_rds_cluster.main.engine_version
  db_subnet_group_name         = aws_db_subnet_group.main.name
  publicly_accessible          = true
  auto_minor_version_upgrade   = true
  performance_insights_enabled = false
}
