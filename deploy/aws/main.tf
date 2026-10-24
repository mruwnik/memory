provider "aws" {
  region = var.region
  default_tags {
    tags = { Application = "memory" }
  }
}

data "aws_caller_identity" "current" {}
data "aws_partition" "current" {}

locals {
  # Bootstrap and the SSM documents both rely on these.
  install_dir = "/opt/memory"
  ssm_prefix  = "/${var.name}"
  account_id  = data.aws_caller_identity.current.account_id
  partition   = data.aws_partition.current.partition
}
