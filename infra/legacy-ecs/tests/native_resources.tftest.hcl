mock_provider "aws" {
  mock_data "aws_caller_identity" { defaults = { account_id = "123456789012" } }
  mock_data "aws_partition" { defaults = { partition = "aws", dns_suffix = "amazonaws.com" } }
  mock_data "aws_availability_zones" { defaults = { names = ["us-east-1a", "us-east-1b"] } }
  mock_resource "aws_iam_role" { defaults = { arn = "arn:aws:iam::123456789012:role/mock-execution" } }
}
mock_provider "aws" { alias = "global" }
mock_provider "random" {}
override_resource {
  target = aws_iam_role.native_execution
  values = { arn = "arn:aws:iam::123456789012:role/native-execution" }
}
override_resource {
  target = aws_cloudwatch_log_group.app
  values = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/kleos/test" }
}
variables {
  region          = "us-east-1"
  name            = "resources-test"
  base_domain     = "example.test"
  certificate_arn = "arn:aws:acm:us-east-1:123456789012:certificate/test"
  frontend_url    = "https://app.example.test"
  public_zone_id  = ""
  control_image   = "123456789012.dkr.ecr.us-east-1.amazonaws.com/control@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  native_images = { for key in ["mechanics", "electronics", "firmware", "application", "validate", "custom.demo@1"] : key =>
    "123456789012.dkr.ecr.us-east-1.amazonaws.com/fixture@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
  }
}

run "shared_profiles_and_capacity_defaults" {
  command = apply # All providers are mocked; this creates no AWS resources.
  plan_options { target = [aws_ecs_task_definition.native, aws_ecs_task_definition.control] }
  assert {
    condition = (
      aws_ecs_task_definition.native["mechanics"].cpu == "1024" && aws_ecs_task_definition.native["mechanics"].memory == "2048" &&
      aws_ecs_task_definition.native["application"].cpu == "4096" && aws_ecs_task_definition.native["application"].memory == "8192" &&
      alltrue([for key in ["electronics", "firmware", "validate", "custom.demo@1"] :
        aws_ecs_task_definition.native[key].cpu == "2048" && aws_ecs_task_definition.native[key].memory == "4096"
      ]) &&
      alltrue([for task in aws_ecs_task_definition.native : task.ephemeral_storage[0].size_in_gib == 40]) &&
      alltrue([for task in aws_ecs_task_definition.native : jsondecode(task.container_definitions)[0].ulimits[0].hardLimit == 256])
    )
    error_message = "Native task sizing must use the shared lane profiles, including the fallback for custom images."
  }
  assert {
    condition = alltrue([
      for task in aws_ecs_task_definition.native : jsondecode(task.container_definitions)[0].readonlyRootFilesystem == true
    ])
    error_message = "Every native task, including application and custom tools, must have a read-only root filesystem."
  }
  assert {
    condition = (
      jsondecode(local.runtime_env.KLEOS_NATIVE_RESOURCE_PROFILES) == jsondecode(file("${path.module}/native-resources.json")) &&
      local.runtime_env.KLEOS_MAX_NATIVE_JOBS == "8" && local.runtime_env.KLEOS_MAX_NATIVE_JOBS_PER_PROJECT == "4" &&
      tonumber(local.runtime_env.KLEOS_NATIVE_JOB_RESERVATION_USD) == 0.10 &&
      local.native_resource_profiles.application.tmpMiB == 6144 &&
      !contains(local.fargate_memory_mib["256"], 1536)
    )
    error_message = "Backend capacity/profile configuration must match Terraform and exclude unsupported Fargate memory combinations."
  }
}

run "operator_overrides_preserve_other_profiles" {
  command = apply
  variables {
    native_resource_overrides = {
      mechanics       = { cpuUnits = 2048, memoryMiB = 8192, tmpMiB = 2048, pidsLimit = 512, ephemeralStorageGiB = 80 }
      "custom.demo@1" = { cpuUnits = 1024, memoryMiB = 3072, tmpMiB = 2048, pidsLimit = 128, ephemeralStorageGiB = 30 }
    }
    max_native_jobs             = 6
    max_native_jobs_per_project = 2
    native_job_reservation_usd  = 0.25
  }
  plan_options { target = [aws_ecs_task_definition.native, aws_ecs_task_definition.control] }
  assert {
    condition = (
      aws_ecs_task_definition.native["mechanics"].cpu == "2048" && aws_ecs_task_definition.native["mechanics"].memory == "8192" &&
      aws_ecs_task_definition.native["mechanics"].ephemeral_storage[0].size_in_gib == 80 &&
      jsondecode(aws_ecs_task_definition.native["mechanics"].container_definitions)[0].ulimits[0].hardLimit == 512 &&
      aws_ecs_task_definition.native["custom.demo@1"].cpu == "1024" && aws_ecs_task_definition.native["custom.demo@1"].memory == "3072" &&
      local.native_resource_profiles.firmware == local.native_resource_defaults.firmware &&
      local.native_resource_profiles._default == local.native_resource_defaults._default &&
      jsondecode(local.runtime_env.KLEOS_NATIVE_RESOURCE_PROFILES)["custom.demo@1"].tmpMiB == 2048 &&
      local.runtime_env.KLEOS_MAX_NATIVE_JOBS == "6" && local.runtime_env.KLEOS_MAX_NATIVE_JOBS_PER_PROJECT == "2" &&
      tonumber(local.runtime_env.KLEOS_NATIVE_JOB_RESERVATION_USD) == 0.25
    )
    error_message = "Overrides must affect only their selected profile and reach both task definitions and backend admission settings."
  }
}

run "reject_invalid_cpu_memory" {
  command = plan
  variables {
    native_resource_overrides = { mechanics = { cpuUnits = 256, memoryMiB = 1536, tmpMiB = 1024, pidsLimit = 256, ephemeralStorageGiB = 40 } }
  }
  plan_options { target = [aws_ecs_task_definition.native] }
  expect_failures = [var.native_resource_overrides]
}
run "reject_scratch_over_memory" {
  command = plan
  variables {
    native_resource_overrides = { mechanics = { cpuUnits = 1024, memoryMiB = 2048, tmpMiB = 4096, pidsLimit = 256, ephemeralStorageGiB = 40 } }
  }
  plan_options { target = [aws_ecs_task_definition.native] }
  expect_failures = [var.native_resource_overrides]
}
run "reject_unbounded_ephemeral_storage" {
  command = plan
  variables {
    native_resource_overrides = { mechanics = { cpuUnits = 1024, memoryMiB = 2048, tmpMiB = 1024, pidsLimit = 256, ephemeralStorageGiB = 201 } }
  }
  plan_options { target = [aws_ecs_task_definition.native] }
  expect_failures = [var.native_resource_overrides]
}
run "reject_unbounded_processes" {
  command = plan
  variables {
    native_resource_overrides = { mechanics = { cpuUnits = 1024, memoryMiB = 2048, tmpMiB = 1024, pidsLimit = 1025, ephemeralStorageGiB = 40 } }
  }
  plan_options { target = [aws_ecs_task_definition.native] }
  expect_failures = [var.native_resource_overrides]
}
run "reject_project_capacity_above_global" {
  command = plan
  variables {
    max_native_jobs             = 4
    max_native_jobs_per_project = 5
  }
  plan_options { target = [aws_ecs_task_definition.control] }
  expect_failures = [var.max_native_jobs_per_project]
}
run "reject_fractional_global_capacity" {
  command = plan
  variables {
    max_native_jobs             = 1.5
    max_native_jobs_per_project = 1
  }
  plan_options { target = [aws_ecs_task_definition.control] }
  expect_failures = [var.max_native_jobs]
}
run "reject_zero_compute_reservation" {
  command = plan
  variables { native_job_reservation_usd = 0 }
  plan_options { target = [aws_ecs_task_definition.control] }
  expect_failures = [var.native_job_reservation_usd]
}
