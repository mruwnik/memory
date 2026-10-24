variable "domain" {
  description = "Hostname the instance serves, e.g. memory.example.com. Point an A record at the public_ip output, or set route53_zone_id."
  type        = string
  validation {
    condition     = can(regex("^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$", var.domain))
    error_message = "domain must be a lowercase hostname."
  }
}

variable "route53_zone_id" {
  description = "Hosted zone to create the domain's A record in. Leave null to add the record yourself."
  type        = string
  default     = null
}

variable "admin_email" {
  description = "Email of the first user. Their generated password is the admin_password output."
  type        = string
  validation {
    condition     = can(regex("^[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+$", var.admin_email))
    error_message = "admin_email must be a plain email address."
  }
}

variable "voyage_api_key" {
  description = "Voyage AI API key for embeddings."
  type        = string
  sensitive   = true
  validation {
    condition     = can(regex("^[A-Za-z0-9._@:/+-]+$", var.voyage_api_key))
    error_message = "voyage_api_key may only contain A-Za-z0-9._@:/+-."
  }
}

variable "region" {
  description = "AWS region for every resource."
  type        = string
  default     = "eu-central-1"
}

variable "availability_zone" {
  description = "AZ for the subnet and data volume. Null takes the region's first available AZ at creation; later changes are ignored, since moving AZ would replace the data volume."
  type        = string
  default     = null
}

variable "instance_type" {
  description = "t3.large suits light use; use t3.xlarge for heavy Claude-session or agent use."
  type        = string
  default     = "t3.large"
}

variable "disk_gb" {
  description = "Size of the data volume holding Docker, the databases and files."
  type        = number
  default     = 100
}

variable "repo_url" {
  description = "Git repository the instance installs and updates from."
  type        = string
  default     = "https://github.com/mruwnik/memory.git"
}

variable "release_signers" {
  description = "SSH public keys (\"ssh-ed25519 AAAA... comment\") whose signed v* tags the instance installs. Removing a key and re-applying is how a signer is revoked."
  type        = list(string)
  validation {
    condition     = length(var.release_signers) > 0 && alltrue([for k in var.release_signers : can(regex("^(ssh-|ecdsa-|sk-)[a-z0-9@.-]+ [A-Za-z0-9+/=]+( [^\\n]*)?$", k))])
    error_message = "release_signers needs at least one SSH public key, one per entry."
  }
}

variable "auto_update" {
  description = "Install new signed releases nightly. Off means releases only arrive by update-to or by hand."
  type        = bool
  default     = true
}

variable "update_delay_hours" {
  description = "A release tag must be at least this old before it installs automatically."
  type        = number
  default     = 48
  validation {
    condition     = var.update_delay_hours >= 0 && floor(var.update_delay_hours) == var.update_delay_hours
    error_message = "update_delay_hours must be a non-negative whole number."
  }
}

variable "backup_retention_days" {
  description = "Days a backup stays current, then the same again as a noncurrent version, so each backup lives about twice this long. Overwritten versions also live this long."
  type        = number
  default     = 30
  validation {
    condition     = var.backup_retention_days >= 1 && floor(var.backup_retention_days) == var.backup_retention_days
    error_message = "backup_retention_days must be a whole number of at least 1."
  }
}

variable "operator_account_id" {
  description = "AWS account allowed to assume the operator roles. Required when any operator_* role is on."
  type        = string
  default     = null
  validation {
    condition     = var.operator_account_id == null || can(regex("^[0-9]{12}$", var.operator_account_id))
    error_message = "operator_account_id must be a 12-digit AWS account id."
  }
  validation {
    condition     = var.operator_account_id != null || !(var.operator_observe || var.operator_maintain || var.operator_recover)
    error_message = "Set operator_account_id to turn on an operator role."
  }
}

variable "operator_observe" {
  description = "Let the operator run status."
  type        = bool
  default     = false
}

variable "operator_maintain" {
  description = "Let the operator run restart, update-to, disk-cleanup and reboot."
  type        = bool
  default     = false
}

variable "operator_recover" {
  description = "Let the operator run rollback and restore-latest-backup, which can lose recent writes."
  type        = bool
  default     = false
}

variable "operator_principal_arns" {
  description = "Principals in operator_account_id that may assume the operator roles. Null trusts the whole account (its root), leaving the choice to that account's IAM."
  type        = list(string)
  default     = null
  validation {
    condition     = var.operator_principal_arns == null || length(coalesce(var.operator_principal_arns, ["x"])) > 0 && alltrue([for a in coalesce(var.operator_principal_arns, ["x"]) : can(regex("^arn:[a-z-]+:iam::${coalesce(var.operator_account_id, "none")}:", a))])
    error_message = "operator_principal_arns must be IAM ARNs in operator_account_id."
  }
}

variable "operator_external_id" {
  description = "If set, assuming an operator role requires this sts:ExternalId."
  type        = string
  default     = null
}
