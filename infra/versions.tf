terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.0" }
  }
}

provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "Kleos9", Stack = var.name, ManagedBy = "Terraform" }
  }
}

# CloudFront certificates must live in us-east-1 whatever the stack region.
provider "aws" {
  alias  = "global"
  region = "us-east-1"
  default_tags {
    tags = { Project = "Kleos9", Stack = var.name, ManagedBy = "Terraform" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
data "aws_availability_zones" "available" { state = "available" }

locals {
  account_id = data.aws_caller_identity.current.account_id
  partition  = data.aws_partition.current.partition
  azs        = slice(data.aws_availability_zones.available.names, 0, 2)
  # Runtime settings and credentials live under this SSM path; Terraform only grants read access.
  ssm_runtime_path = "/${var.name}/runtime"
  ssm_host_path    = "/${var.name}/host"
  api_enabled      = var.control_image != ""
}
