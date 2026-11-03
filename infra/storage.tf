# Artifacts: immutable content-addressed bytes shared by the API Lambda and the host.
resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.name}-artifacts-"
  force_destroy = false
}
# Toolchains: native image archives (images/<image-id-hex>.tar.zst, from `docker save | zstd`) and
# their qualification evidence (qualification/). Cheaper than ECR for ~30 GiB of rarely changing
# images; `docker load` preserves the image ID that qualification evidence pins.
resource "aws_s3_bucket" "toolchains" {
  bucket_prefix = "${var.name}-toolchains-"
  force_destroy = false
}

locals {
  private_buckets = { artifacts = aws_s3_bucket.artifacts, toolchains = aws_s3_bucket.toolchains }
}
resource "aws_s3_bucket_public_access_block" "private" {
  for_each                = local.private_buckets
  bucket                  = each.value.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "private" {
  for_each = local.private_buckets
  bucket   = each.value.id
  versioning_configuration { status = "Enabled" }
}
# SSE-S3 rather than a customer-managed KMS key ($1/month each plus request charges).
resource "aws_s3_bucket_server_side_encryption_configuration" "private" {
  for_each = local.private_buckets
  bucket   = each.value.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_policy" "private" {
  for_each = local.private_buckets
  bucket   = each.value.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Deny", Principal = "*", Action = "s3:*",
    Resource  = [each.value.arn, "${each.value.arn}/*"],
    Condition = { Bool = { "aws:SecureTransport" = "false" } }
  }] })
}
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "toolchains" {
  bucket = aws_s3_bucket.toolchains.id
  rule {
    id     = "superseded-archives"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

# Lambda can only run images from ECR, so the control image (~0.5 GiB) stays there.
resource "aws_ecr_repository" "control" {
  name                 = "${var.name}/control"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration { scan_on_push = true }
  encryption_configuration { encryption_type = "AES256" }
}
resource "aws_ecr_lifecycle_policy" "control" {
  repository = aws_ecr_repository.control.name
  policy = jsonencode({ rules = [{
    rulePriority = 1, description = "Keep the 10 most recent releases",
    selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 10 },
    action       = { type = "expire" }
  }] })
}
resource "aws_ecr_repository_policy" "control" {
  repository = aws_ecr_repository.control.name
  policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Sid       = "LambdaPull", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" },
    Action    = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer"],
    Condition = { StringLike = { "aws:sourceArn" = "arn:${local.partition}:lambda:${var.region}:${local.account_id}:function:${var.name}-*" } }
  }] })
}
