output "public_ip" {
  description = "Point the domain's A record here (done for you with route53_zone_id)."
  value       = aws_eip.this.public_ip
}

output "url" {
  value = "https://${var.domain}"
}

output "admin_email" {
  value = var.admin_email
}

output "admin_password" {
  description = "First user's password: terraform output -raw admin_password"
  value       = random_password.secret["admin_password"].result
  sensitive   = true
}

output "instance_id" {
  value = aws_instance.this.id
}

output "instance_arn" {
  value = local.instance_arn
}

output "instance_role_arn" {
  value = aws_iam_role.instance.arn
}

output "kms_key_arn" {
  value = aws_kms_key.this.arn
}

output "backup_bucket" {
  value = aws_s3_bucket.backups.id
}

output "ops_output_bucket" {
  description = "Where operators send and read command output."
  value       = aws_s3_bucket.ops_output.id
}

output "ssm_prefix" {
  value = local.ssm_prefix
}

output "documents" {
  description = "SSM document name per action. Names change when a script changes."
  value       = { for a, d in aws_ssm_document.op : a => d.name }
}

output "document_arns" {
  value = { for a, d in aws_ssm_document.op : a => d.arn }
}

output "operator_role_arns" {
  description = "Roles the operator account can assume; only the enabled ones."
  value       = { for r, role in aws_iam_role.operator : r => role.arn }
}

output "operator_actions" {
  value = local.enabled_roles
}
