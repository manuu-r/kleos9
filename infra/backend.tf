terraform {
  backend "s3" {
    bucket = "kleos9-tfstate-750420487076-us-east-1"
    # Deliberately NOT kleos9/terraform.tfstate: that key still tracks the legacy ECS stack
    # (infra/legacy-ecs). Reusing it would plan the destruction of the running deployment.
    key          = "kleos9/serverless.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
