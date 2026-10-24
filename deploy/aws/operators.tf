locals {
  ops_dir = "${path.module}/../../tools/ops"

  # Destructive actions get --yes: invoking the document is the confirmation.
  # Parameter patterns are the only thing between the caller and the shell
  # line, so they admit no metacharacters and no leading dash.
  actions = {
    status = { role = "observe", args = "", parameters = {} }
    logs = {
      role       = "observe"
      args       = "{{ lines }}"
      parameters = { lines = { type = "String", default = "100", allowedPattern = "^[1-9][0-9]{0,3}$", description = "Error lines per service, at most 1000" } }
    }
    restart = {
      role       = "maintain"
      args       = "{{ service }}"
      parameters = { service = { type = "String", default = "", allowedPattern = "^([a-z0-9][a-z0-9_-]{0,63})?$", description = "Compose service; empty restarts all" } }
    }
    update-to = {
      role       = "maintain"
      args       = "{{ tag }}"
      parameters = { tag = { type = "String", allowedPattern = "^v[0-9]+\\.[0-9]+\\.[0-9]+$", description = "Release tag, vX.Y.Z" } }
    }
    disk-cleanup          = { role = "maintain", args = "", parameters = {} }
    reboot                = { role = "maintain", args = "--yes", parameters = {} }
    rollback              = { role = "recover", args = "--yes", parameters = {} }
    restore-latest-backup = { role = "recover", args = "--yes", parameters = {} }
  }

  document_content = { for a, v in local.actions : a => yamlencode({
    schemaVersion = "2.2"
    description   = "memory: ${a}"
    parameters    = v.parameters
    mainSteps = [{
      action = "aws:runShellScript"
      name   = replace(a, "-", "_")
      inputs = {
        timeoutSeconds = 3600
        runCommand = [templatefile("${path.module}/ops-document.sh.tftpl", {
          action      = a
          args        = v.args
          install_dir = local.install_dir
          lib         = file("${local.ops_dir}/lib.sh")
          script      = file("${local.ops_dir}/${a}.sh")
        })]
      }
    }]
  }) }

  roles = {
    observe  = var.operator_observe
    maintain = var.operator_maintain
    recover  = var.operator_recover
  }
  enabled_roles = { for r, on in local.roles : r => [for a, v in local.actions : a if v.role == r] if on }

  instance_arn = "arn:${local.partition}:ec2:${var.region}:${local.account_id}:instance/${aws_instance.this.id}"
  ops_output   = [aws_s3_bucket.ops_output.arn, "${aws_s3_bucket.ops_output.arn}/*"]
}

# The scripts are embedded, not run from the checkout, so a release can't
# change what a document does without the owner re-applying and seeing the diff.
# The content hash in the name makes each change a new document and deletes the
# old one, because SendCommand will run any version a document still has.
resource "aws_ssm_document" "op" {
  for_each        = local.document_content
  name            = "memory-${each.key}-${substr(sha256(each.value), 0, 8)}"
  document_type   = "Command"
  document_format = "YAML"
  content         = each.value

  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = !strcontains("${file("${local.ops_dir}/lib.sh")}${file("${local.ops_dir}/${each.key}.sh")}", "{{")
      error_message = "tools/ops/lib.sh and ${each.key}.sh must not contain \"{{\": SSM reads it as a document parameter."
    }
  }
}

resource "aws_iam_role" "operator" {
  for_each = local.enabled_roles
  name     = "memory-${each.key}"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [merge(
      {
        Effect    = "Allow"
        Action    = "sts:AssumeRole"
        Principal = { AWS = coalesce(var.operator_principal_arns, ["arn:${local.partition}:iam::${var.operator_account_id}:root"]) }
      },
      var.operator_external_id == null ? {} : { Condition = { StringEquals = { "sts:ExternalId" = var.operator_external_id } } },
    )]
  })
}

# Command output is read only from the ops-output bucket: GetCommandInvocation
# and its kin can't be scoped, and would show every Run Command in the account.
# The denies cap the role, so a broader policy attached later can't widen it.
resource "aws_iam_role_policy" "operator" {
  for_each = local.enabled_roles
  role     = aws_iam_role.operator[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(
      [
        {
          Sid      = "RunOwnDocuments"
          Effect   = "Allow"
          Action   = "ssm:SendCommand"
          Resource = concat([local.instance_arn], [for a in each.value : aws_ssm_document.op[a].arn])
        },
        {
          Sid      = "DescribeInstances"
          Effect   = "Allow"
          Action   = "ec2:DescribeInstances"
          Resource = "*"
        },
        {
          Sid      = "ReadCommandOutput"
          Effect   = "Allow"
          Action   = ["s3:GetObject", "s3:ListBucket"]
          Resource = local.ops_output
        },
      ],
      each.key == "maintain" ? [{
        Sid      = "Reboot"
        Effect   = "Allow"
        Action   = "ec2:RebootInstances"
        Resource = local.instance_arn
      }] : [],
      [
        {
          Sid       = "DenyEverythingElse"
          Effect    = "Deny"
          NotAction = concat(["ssm:SendCommand", "ec2:DescribeInstances", "s3:GetObject", "s3:ListBucket"], each.key == "maintain" ? ["ec2:RebootInstances"] : [])
          Resource  = "*"
        },
        {
          Sid         = "DenyOtherDocumentsAndInstances"
          Effect      = "Deny"
          Action      = concat(["ssm:SendCommand"], each.key == "maintain" ? ["ec2:RebootInstances"] : [])
          NotResource = concat([local.instance_arn], [for a in each.value : aws_ssm_document.op[a].arn])
        },
        {
          Sid         = "DenyOtherBuckets"
          Effect      = "Deny"
          Action      = ["s3:GetObject", "s3:ListBucket"]
          NotResource = local.ops_output
        },
      ],
    )
  })
}
