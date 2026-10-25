# Names are the lowercased environment variables install.sh and the ops
# scripts read, so bootstrap maps them one to one.

# install.sh only accepts A-Za-z0-9._@:/+- in these.
resource "random_password" "secret" {
  for_each = toset([
    "admin_password",
    "postgres_password",
    "redis_password",
    "secrets_encryption_key",
    "backup_encryption_key",
  ])
  length  = 32
  special = false
}

locals {
  # tools/ops/rotate-keys.sh may replace these on the box.
  rotated_secrets = ["secrets_encryption_key", "postgres_password", "redis_password", "backup_encryption_key"]

  config = {
    domain             = var.domain
    admin_email        = var.admin_email
    repo_url           = var.repo_url
    auto_update        = tostring(var.auto_update)
    update_delay_hours = tostring(var.update_delay_hours)
    # allowed_signers format; the namespace stops an SSH key's other signatures
    # (commits, files) from passing as tag signatures.
    release_signers  = join("\n", [for k in var.release_signers : "* namespaces=\"git\" ${trimspace(k)}"])
    s3_backup_bucket = aws_s3_bucket.backups.id
    s3_backup_prefix = "memory"
    s3_backup_region = var.region
  }
}

# Only the first value comes from here: once rotate-keys.sh has replaced one,
# the box's value is the live one, and an apply must not put the old one back.
resource "aws_ssm_parameter" "secret" {
  for_each = toset(keys(random_password.secret))
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "SecureString"
  key_id   = aws_kms_key.this.arn
  value    = random_password.secret[each.key].result

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "voyage_api_key" {
  name   = "${local.ssm_prefix}/voyage_api_key"
  type   = "SecureString"
  key_id = aws_kms_key.this.arn
  value  = var.voyage_api_key
}

moved {
  from = aws_ssm_parameter.secret["voyage_api_key"]
  to   = aws_ssm_parameter.voyage_api_key
}

# Re-read by the box before each update, so changing release_signers or the
# delay takes effect without replacing the instance.
resource "aws_ssm_parameter" "config" {
  for_each = local.config
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "String"
  value    = each.value
}
