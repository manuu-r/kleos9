terraform {
  backend "s3" {
    bucket       = "kleos9-tfstate-750420487076-us-east-1"
    key          = "kleos9/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
