terraform {
  required_version = ">= 1.6"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 6.0" }
    random = { source = "hashicorp/random", version = "~> 3.7" }
  }
}
provider "aws" {
  region = var.region
  default_tags {
    tags = { Project = "Kleos9", Environment = var.environment, ManagedBy = "Terraform" }
  }
}
variable "region" {
  type    = string
  default = "us-east-1"
}
variable "environment" {
  type    = string
  default = "staging"
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "AWS deployments must use staging or production runtime safeguards."
  }
}
variable "name" {
  type    = string
  default = "kleos9-demo"
}
variable "base_domain" {
  type        = string
  description = "Domain whose TLS certificate covers api.DOMAIN and jobs.DOMAIN."
}
variable "public_zone_id" {
  type        = string
  description = "Existing Route53 hosted zone for base_domain."
}
variable "certificate_arn" {
  type        = string
  description = "Issued ACM certificate in this region for api and jobs hostnames."
}
variable "frontend_url" {
  type        = string
  description = "HTTPS URL of the deployed Flutter web app."
}
variable "control_image" {
  type        = string
  description = "ECR control image pinned by sha256 digest."
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.control_image))
    error_message = "Use an immutable image digest."
  }
}
variable "native_images" {
  type        = map(string)
  default     = {}
  description = "Qualified ECR image digests keyed mechanics/electronics/firmware/application/validate."
  validation {
    condition = alltrue([for key, image in var.native_images :
      contains(["mechanics", "electronics", "firmware", "application", "validate"], key)
    && can(regex("@sha256:[a-f0-9]{64}$", image))])
    error_message = "Native images must have known lane keys and immutable digests."
  }
}
variable "recovery_cluster_arns" {
  type        = list(string)
  default     = []
  description = "Previous ECS clusters retained until their pinned native jobs drain."
}
variable "recovery_task_definition_arns" {
  type        = list(string)
  default     = []
  description = "Exact previous native task-definition revisions needed for durable recovery."
}
variable "recovery_execution_role_arns" {
  type        = list(string)
  default     = []
  description = "Previous native execution roles used by recovery task definitions; never grant generated code a task role."
}
variable "conductor_image" {
  type    = string
  default = ""
}
variable "conductor_license_secret_arn" {
  type        = string
  default     = ""
  description = "Existing Secrets Manager secret containing the Conductor license."
}
variable "activate_services" {
  type        = bool
  default     = false
  description = "Enable only after database bootstrap, migrations and runtime secrets are configured."
}
variable "database_multi_az" {
  type    = bool
  default = false
}
variable "project_budget_usd" {
  type    = number
  default = 0
}
variable "daily_budget_usd" {
  type    = number
  default = 0
}
variable "alert_email" {
  type    = string
  default = ""
}
data "aws_availability_zones" "available" { state = "available" }
data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}
locals {
  azs               = slice(data.aws_availability_zones.available.names, 0, 2)
  api_hostname      = "api.${var.base_domain}"
  jobs_hostname     = "jobs.${var.base_domain}"
  conductor_enabled = var.conductor_license_secret_arn != "" && var.conductor_image != ""
}
