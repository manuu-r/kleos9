terraform {
  required_version = ">= 1.10"
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
  default     = ""
  description = "Route53 public zone ID, or empty when public DNS is managed externally (for example Cloudflare)."
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
  description = "Qualified ECR image digests keyed by a default lane or engineering tool id@version."
  validation {
    condition = alltrue([for key, image in var.native_images :
      (contains(["mechanics", "electronics", "firmware", "application", "validate"], key)
      || can(regex("^[a-z][a-z0-9.-]{0,79}@[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$", key)))
    && can(regex("@sha256:[a-f0-9]{64}$", image))])
    error_message = "Native images require default lane or id@version keys and immutable digests."
  }
}
variable "native_resource_overrides" {
  type = map(object({
    cpuUnits            = number
    memoryMiB           = number
    tmpMiB              = number
    pidsLimit           = number
    ephemeralStorageGiB = number
  }))
  default     = {}
  description = "Complete resource profiles keyed by lane, _default or qualified tool id@version; merged over the shared native-resources.json defaults."
  validation {
    condition = alltrue([for key, profile in var.native_resource_overrides : try(
      (contains(["_default", "mechanics", "electronics", "firmware", "application", "validate"], key)
      || can(regex("^[a-z][a-z0-9.-]{0,79}@[A-Za-z0-9][A-Za-z0-9_.-]{0,49}$", key))) &&
      alltrue([for value in values(profile) : value > 0 && floor(value) == value]) &&
      contains(lookup(local.fargate_memory_mib, tostring(profile.cpuUnits), []), profile.memoryMiB) &&
      profile.tmpMiB <= profile.memoryMiB && profile.pidsLimit >= 32 && profile.pidsLimit <= 1024 &&
      profile.ephemeralStorageGiB >= 21 && profile.ephemeralStorageGiB <= 200,
      false
    )])
    error_message = "Native profiles require valid keys, positive integers, a supported Fargate CPU/memory pair (CPU <=16384), tmpMiB <= memoryMiB, 32..1024 processes and 21..200 GiB ephemeral storage."
  }
}
variable "max_native_jobs" {
  type        = number
  default     = 8
  description = "Global concurrent native-job admission ceiling; queued work remains visible."
  validation {
    condition     = var.max_native_jobs >= 1 && floor(var.max_native_jobs) == var.max_native_jobs
    error_message = "max_native_jobs must be a positive integer."
  }
}
variable "max_native_jobs_per_project" {
  type        = number
  default     = 4
  description = "Per-project concurrent native-job ceiling, no greater than the global limit."
  validation {
    condition = (var.max_native_jobs_per_project >= 1 && floor(var.max_native_jobs_per_project) == var.max_native_jobs_per_project
    && var.max_native_jobs_per_project <= var.max_native_jobs)
    error_message = "Per-project native capacity must be a positive integer no greater than max_native_jobs."
  }
}
variable "native_job_reservation_usd" {
  type        = number
  default     = 0.10
  description = "Conservative admission reservation per native attempt; an allowance debit, not an AWS price or billing estimate."
  validation {
    condition     = var.native_job_reservation_usd > 0
    error_message = "Native-job allowance reservations must be positive."
  }
}
variable "engineering_tools" {
  type = list(object({
    id                     = string
    version                = string
    name                   = string
    lane                   = string
    runner                 = string
    sourceContract         = string
    outputFiles            = list(string)
    qualified              = bool
    qualificationReference = optional(string)
    tenantIds              = optional(list(string), [])
    limitations            = optional(list(string), [])
  }))
  default     = []
  description = "Operator-reviewed tool contracts; install matching immutable native_images[id@version]."
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

variable "native_sessions_enabled" {
  type        = bool
  default     = false
  description = "Enable bounded model/tool sessions after qualifying images and draining incompatible workflow versions."
}
