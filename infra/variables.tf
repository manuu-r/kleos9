variable "region" {
  type    = string
  default = "us-east-1"
}
variable "name" {
  type        = string
  default     = "kleos9"
  description = "Resource name prefix. Must differ from the legacy stack's kleos9-production while that stack exists."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,30}$", var.name)) && var.name != "kleos9-production"
    error_message = "Use a short lowercase name that does not collide with the legacy kleos9-production stack."
  }
}
variable "app_hostname" {
  type        = string
  description = "Public workbench hostname served by CloudFront, for example app.kleos9.com."
}
variable "api_hostname" {
  type        = string
  description = "Public API hostname served by CloudFront in front of the API Lambda, for example api.kleos9.com."
}
variable "preview_hostname" {
  type        = string
  description = "Separate companion preview origin, for example preview.kleos9.com. Production settings require it even while previews are disabled."
  validation {
    condition     = var.preview_hostname != ""
    error_message = "Generated previews require their own hostname."
  }
}
variable "preview_runtime_parameters" {
  type        = list(string)
  default     = ["KLEOS_DATABASE_URL", "KLEOS_SESSION_SECRET"]
  description = "The only SSM runtime parameters the preview process may read."
  validation {
    condition     = length(setintersection(var.preview_runtime_parameters, ["KLEOS_META_API_KEY", "KLEOS_MODAL_API_KEY", "KLEOS_MODAL_LLM_KEY", "KLEOS_SETUP_TOKEN"])) == 0
    error_message = "The preview process never receives provider credentials or the setup token."
  }
}
variable "control_image" {
  type        = string
  default     = ""
  description = "Initial control image (ECR, pinned by sha256 digest). Empty provisions storage, database and the host but no API. Releases from an operator workstation update the image outside Terraform."
  validation {
    condition     = var.control_image == "" || can(regex("^[0-9]{12}[.]dkr[.]ecr[.][a-z0-9-]+[.]amazonaws[.]com/[a-z0-9._/-]+@sha256:[a-f0-9]{64}$", var.control_image))
    error_message = "Use a private ECR image pinned by sha256 digest."
  }
}

# --- API Lambda ---
variable "api_memory_mb" {
  type    = number
  default = 1024
}
variable "api_reserved_concurrency" {
  type        = number
  default     = null
  description = "Optional ceiling on concurrent API invocations (each open event stream holds one). Null leaves it unreserved."
}
variable "companion_preview_enabled" {
  type    = bool
  default = false
}

# --- Engineering host (workers + Docker for native jobs) ---
variable "host_instance_type" {
  type        = string
  default     = "m6a.xlarge"
  description = "x86_64 type; native images are linux/amd64. The Flutter web lane alone needs ~10 GiB (6 GiB RAM + 4 GiB tmpfs)."
}
variable "host_volume_gib" {
  type        = number
  default     = 60
  description = "Root gp3 volume holding the Docker image cache (~30 GiB of native images). Billed while stopped."
  validation {
    condition     = var.host_volume_gib >= 30 && var.host_volume_gib <= 500
    error_message = "Use 30-500 GiB."
  }
}
variable "host_worker_commands" {
  type        = list(string)
  default     = ["worker"]
  description = "kleos.cli worker commands run on the host. `worker` already includes verification, external waits and native/MCP maintenance."
  validation {
    condition     = length(var.host_worker_commands) > 0 && alltrue([for c in var.host_worker_commands : contains(["worker", "fabrication-worker", "verification-worker", "native-maintenance", "external-worker"], c)])
    error_message = "Use known kleos.cli worker commands."
  }
}
variable "host_idle_minutes" {
  type        = number
  default     = 10
  description = "Consecutive idle minutes (per `kleos.cli idle-check`) before the host powers itself off."
  validation {
    condition     = var.host_idle_minutes >= 3 && var.host_idle_minutes <= 120
    error_message = "Use 3-120 minutes."
  }
}
variable "host_max_session_minutes" {
  type        = number
  default     = 240
  description = "Cost guard used only while idle-check is unavailable or failing: power off after this uptime."
  validation {
    condition     = var.host_max_session_minutes >= 30
    error_message = "Use at least 30 minutes."
  }
}
variable "external_wait_tick_minutes" {
  type        = number
  default     = 60
  description = "While external waits are outstanding and the host is stopped, start it this often to observe them."
  validation {
    condition     = var.external_wait_tick_minutes >= 15
    error_message = "Use at least 15 minutes; each tick boots the host for roughly idle_minutes plus boot time."
  }
}

# --- Database (Aurora Serverless v2, pauses at 0 ACU) ---
variable "database_engine_version" {
  type        = string
  default     = "16.6"
  description = "Aurora PostgreSQL version; 0-ACU auto-pause needs 13.15+, 14.12+, 15.7+ or 16.3+."
}
variable "database_max_acu" {
  type    = number
  default = 4
}
variable "database_auto_pause_seconds" {
  type    = number
  default = 300
  validation {
    condition     = var.database_auto_pause_seconds >= 300 && var.database_auto_pause_seconds <= 86400
    error_message = "Aurora accepts 300-86400 seconds."
  }
}
variable "database_ingress_cidrs" {
  type        = list(string)
  default     = ["0.0.0.0/0"]
  description = "The API Lambda runs outside any VPC (it needs internet without a NAT), so its source IPs are not fixed. TLS is enforced."
}

# --- Operations ---
variable "alert_email" {
  type    = string
  default = ""
}
variable "monthly_budget_usd" {
  type    = number
  default = 50
}
variable "log_retention_days" {
  type    = number
  default = 14
}
