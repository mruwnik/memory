# Names are the lowercased environment variables install.sh and the ops
# scripts read, so bootstrap maps them one to one.

# install.sh only accepts A-Za-z0-9._@:/+- in these.
resource "random_password" "secret" {
  for_each = toset([
    "admin_password",
    "postgres_password",
    "jwt_secret",
    "redis_password",
    "secrets_encryption_key",
    "backup_encryption_key",
  ])
  length  = 32
  special = false
}

locals {
  secrets = merge(
    { for k, v in random_password.secret : k => v.result },
    { voyage_api_key = var.voyage_api_key },
  )

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

resource "aws_ssm_parameter" "secret" {
  for_each = nonsensitive(toset(keys(local.secrets)))
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "SecureString"
  key_id   = aws_kms_key.this.arn
  value    = local.secrets[each.key]
}

# Re-read by the box before each update, so changing release_signers or the
# delay takes effect without replacing the instance.
resource "aws_ssm_parameter" "config" {
  for_each = local.config
  name     = "${local.ssm_prefix}/${each.key}"
  type     = "String"
  value    = each.value
}
