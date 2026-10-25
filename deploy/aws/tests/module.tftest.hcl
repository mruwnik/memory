mock_provider "aws" {
  mock_data "aws_availability_zones" {
    defaults = { names = ["eu-central-1a"] }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "111111111111" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  # Mocked ARNs must still pass the provider's ARN validation.
  mock_resource "aws_kms_key" {
    defaults = { arn = "arn:aws:kms:eu-central-1:111111111111:key/mock" }
  }
}

mock_provider "random" {}

variables {
  domain          = "memory.example.com"
  admin_email     = "owner@example.com"
  voyage_api_key  = "pa-test"
  release_signers = ["ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExample signer@example.com"]
}

run "instance_is_locked_down" {
  command = plan

  assert {
    condition     = aws_instance.this.metadata_options[0].http_tokens == "required" && aws_instance.this.metadata_options[0].http_put_response_hop_limit == 1
    error_message = "IMDSv2 must be required with hop limit 1"
  }
  assert {
    condition     = toset([for r in aws_security_group.this.ingress : "${r.protocol}/${r.from_port}-${r.to_port}"]) == toset(["tcp/80-80", "tcp/443-443"])
    error_message = "ingress must be TCP 80 and 443 only"
  }
  assert {
    condition     = aws_instance.this.root_block_device[0].encrypted && aws_ebs_volume.data.encrypted
    error_message = "both volumes must be encrypted"
  }
}

run "backups_are_private_and_encrypted" {
  command = plan

  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.backups.block_public_acls,
      aws_s3_bucket_public_access_block.backups.block_public_policy,
      aws_s3_bucket_public_access_block.backups.ignore_public_acls,
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets,
    ])
    error_message = "the bucket must block all public access"
  }
  assert {
    condition     = one(one(aws_s3_bucket_server_side_encryption_configuration.backups.rule).apply_server_side_encryption_by_default).sse_algorithm == "aws:kms"
    error_message = "the bucket must use SSE-KMS"
  }
  assert {
    condition     = one(aws_s3_bucket_versioning.backups.versioning_configuration).status == "Enabled"
    error_message = "the bucket must be versioned"
  }
}

run "no_command_output_in_backups" {
  assert {
    condition = anytrue([
      for st in jsondecode(aws_iam_role_policy.instance.policy).Statement :
      st.Effect == "Deny" && st.Action == "s3:PutObject" && st.Resource == "${aws_s3_bucket.backups.arn}/*/awsrunShellScript/*"
    ])
    error_message = "the instance must refuse to write Run Command output into the backups"
  }
}

run "no_operator_roles_by_default" {
  command = plan

  assert {
    condition     = length(aws_iam_role.operator) == 0
    error_message = "operator roles must be off by default"
  }
}

run "operator_role_needs_account" {
  command = plan
  variables {
    operator_observe = true
  }
  expect_failures = [var.operator_account_id]
}

run "only_observe" {
  variables {
    operator_account_id = "222222222222"
    operator_observe    = true
  }
  assert {
    condition     = keys(aws_iam_role.operator) == ["observe"]
    error_message = "only memory-observe should exist"
  }
}

run "only_maintain" {
  variables {
    operator_account_id = "222222222222"
    operator_maintain   = true
  }
  assert {
    condition     = keys(aws_iam_role.operator) == ["maintain"]
    error_message = "only memory-maintain should exist"
  }
}

run "only_recover" {
  variables {
    operator_account_id = "222222222222"
    operator_recover    = true
  }
  assert {
    condition     = keys(aws_iam_role.operator) == ["recover"]
    error_message = "only memory-recover should exist"
  }
}

run "all_roles" {
  variables {
    operator_account_id = "222222222222"
    operator_observe    = true
    operator_maintain   = true
    operator_recover    = true
  }

  assert {
    condition     = jsondecode(aws_iam_role.operator["observe"].assume_role_policy).Statement[0].Principal.AWS == ["arn:aws:iam::222222222222:root"]
    error_message = "roles must be assumable by the operator account only"
  }
  assert {
    condition     = !can(jsondecode(aws_iam_role.operator["observe"].assume_role_policy).Statement[0].Condition)
    error_message = "no ExternalId condition unless operator_external_id is set"
  }
  assert {
    condition = alltrue([
      for r, docs in {
        observe  = ["status", "logs"]
        maintain = ["restart", "update-to", "disk-cleanup", "reboot"]
        recover  = ["rollback", "restore-latest-backup", "rotate-keys"]
      } :
      toset([for s in jsondecode(aws_iam_role_policy.operator[r].policy).Statement : s.Resource if s.Sid == "RunOwnDocuments"][0]) ==
      toset(concat([output.instance_arn], [for d in docs : aws_ssm_document.op[d].arn]))
    ])
    error_message = "each role may send only its own documents, to this instance only"
  }
  assert {
    condition = alltrue([
      for r in ["observe", "maintain", "recover"] :
      toset(one([for s in jsondecode(aws_iam_role_policy.operator[r].policy).Statement : s.NotAction if s.Sid == "DenyEverythingElse"])) ==
      toset(flatten([for s in jsondecode(aws_iam_role_policy.operator[r].policy).Statement : s.Action if s.Effect == "Allow"]))
    ])
    error_message = "the NotAction deny must leave exactly the allowed actions, so a broader policy can't add any"
  }
  assert {
    condition = toset(flatten([for s in jsondecode(aws_iam_role_policy.operator["observe"].policy).Statement : s.Action if s.Effect == "Allow"])) == toset(
      ["ssm:SendCommand", "ec2:DescribeInstances", "s3:GetObject", "s3:ListBucket"]
    )
    error_message = "observe may only send commands, describe instances and read the output bucket (no GetCommandInvocation)"
  }
  assert {
    condition = alltrue(flatten([
      for r in ["observe", "maintain", "recover"] : [
        for s in jsondecode(aws_iam_role_policy.operator[r].policy).Statement :
        toset(try(s.Resource, s.NotResource)) == toset([aws_s3_bucket.ops_output.arn, "${aws_s3_bucket.ops_output.arn}/*"])
        if contains(["ReadCommandOutput", "DenyOtherBuckets"], s.Sid)
      ]
    ]))
    error_message = "S3 reads must be limited to the ops-output bucket"
  }
  assert {
    condition     = alltrue([for a, d in aws_ssm_document.op : d.name == "memory-${a}-${substr(sha256(d.content), 0, 8)}"])
    error_message = "document names must carry their content hash"
  }
  assert {
    condition = [
      for r in ["observe", "maintain", "recover"] :
      contains([for s in jsondecode(aws_iam_role_policy.operator[r].policy).Statement : s.Sid], "Reboot")
    ] == [false, true, false]
    error_message = "only memory-maintain may reboot the instance"
  }
  assert {
    condition = {
      for a, d in aws_ssm_document.op : a => endswith(trimspace(yamldecode(d.content).mainSteps[0].inputs.runCommand[0]), "--yes")
      } == {
      status = false, logs = false, restart = false, update-to = false, disk-cleanup = false
      reboot = true, rollback = true, restore-latest-backup = true, rotate-keys = true
    }
    error_message = "exactly the destructive documents pass --yes"
  }
  assert {
    condition = toset([
      for a, d in aws_ssm_document.op : a if yamldecode(d.content).mainSteps[0].inputs.timeoutSeconds == 14400
    ]) == toset(["update-to", "rollback", "restore-latest-backup", "rotate-keys"])
    error_message = "every document that takes a full pg_dump gets 14400 s: SIGKILL skips the failure traps"
  }
}

run "trust_narrowed" {
  variables {
    operator_account_id     = "222222222222"
    operator_observe        = true
    operator_principal_arns = ["arn:aws:iam::222222222222:role/oncall"]
    operator_external_id    = "memory-ops"
  }
  assert {
    condition     = jsondecode(aws_iam_role.operator["observe"].assume_role_policy).Statement[0].Principal.AWS == ["arn:aws:iam::222222222222:role/oncall"]
    error_message = "operator_principal_arns must replace the account root"
  }
  assert {
    condition     = jsondecode(aws_iam_role.operator["observe"].assume_role_policy).Statement[0].Condition.StringEquals["sts:ExternalId"] == "memory-ops"
    error_message = "operator_external_id must be required"
  }
}

run "principal_outside_operator_account" {
  command = plan
  variables {
    operator_account_id     = "222222222222"
    operator_observe        = true
    operator_principal_arns = ["arn:aws:iam::333333333333:role/oncall"]
  }
  expect_failures = [var.operator_principal_arns]
}

run "restart_service_pattern" {
  command = plan
  assert {
    condition = [
      for v in ["", "api", "agents-worker", "--dry-run", "-x", "api;id", "a b"] :
      can(regex(yamldecode(aws_ssm_document.op["restart"].content).parameters.service.allowedPattern, v))
    ] == [true, true, true, false, false, false, false]
    error_message = "service must be empty or a plain name with no leading dash"
  }
}

run "rotate_keys_names_pattern" {
  command = plan
  assert {
    condition = [
      for v in ["", "REDIS_PASSWORD", "REDIS_PASSWORD,POSTGRES_PASSWORD", "--yes", "A B", "A;id", "A,", "redis_password"] :
      can(regex(yamldecode(aws_ssm_document.op["rotate-keys"].content).parameters.only.allowedPattern, v))
    ] == [true, true, true, false, false, false, false, false]
    error_message = "only must be empty or comma-separated upper-case key names"
  }
}

run "instance_writes_only_the_secrets_it_rotates" {
  assert {
    condition = toset(one([for s in jsondecode(aws_iam_role_policy.instance.policy).Statement : s.Resource if s.Sid == "RotateOwnKeys"])) == toset(concat(
      [for k in ["secrets_encryption_key", "postgres_password", "redis_password", "backup_encryption_key"] : "arn:aws:ssm:eu-central-1:111111111111:parameter/memory/${k}"],
      ["arn:aws:ssm:eu-central-1:111111111111:parameter/memory/previous/*"],
    ))
    error_message = "the instance may write only the generated secrets and their superseded copies, never config or admin_password"
  }
  assert {
    condition     = one([for s in jsondecode(aws_iam_role_policy.instance.policy).Statement : s.Action if s.Sid == "RotateOwnKeys"]) == "ssm:PutParameter"
    error_message = "RotateOwnKeys must grant only ssm:PutParameter"
  }
  assert {
    condition     = !anytrue([for s in jsondecode(aws_iam_role_policy.instance.policy).Statement : contains(flatten([s.Action]), "ssm:DeleteParameter") if s.Effect == "Allow"])
    error_message = "the instance must not delete parameters: the kept keys under previous/ are the only copy some backups have"
  }
}

run "availability_zone_chosen_at_creation" {
  state_key = "fresh"
  variables {
    availability_zone = "eu-central-1b"
  }
  assert {
    condition     = aws_subnet.this.availability_zone == "eu-central-1b" && aws_ebs_volume.data.availability_zone == "eu-central-1b"
    error_message = "availability_zone must place the subnet and data volume"
  }
}

# Changing the AZ later (or a reordered AZ list) must not move the data volume.
run "availability_zone_is_sticky" {
  state_key = "fresh"
  command   = plan
  variables {
    availability_zone = "eu-central-1c"
  }
  assert {
    condition     = aws_subnet.this.availability_zone == "eu-central-1b" && aws_ebs_volume.data.availability_zone == "eu-central-1b"
    error_message = "the subnet and data volume must stay in their original AZ"
  }
}

run "ops_output_bucket_is_private" {
  command = plan
  assert {
    condition = alltrue([
      aws_s3_bucket_public_access_block.ops_output.block_public_acls,
      aws_s3_bucket_public_access_block.ops_output.block_public_policy,
      aws_s3_bucket_public_access_block.ops_output.ignore_public_acls,
      aws_s3_bucket_public_access_block.ops_output.restrict_public_buckets,
      one(one(aws_s3_bucket_server_side_encryption_configuration.ops_output.rule).apply_server_side_encryption_by_default).sse_algorithm == "AES256",
    ])
    error_message = "the ops-output bucket must be encrypted and block public access"
  }
}

run "empty_principal_list_rejected" {
  command = plan
  variables {
    operator_account_id     = "222222222222"
    operator_observe        = true
    operator_principal_arns = []
  }
  expect_failures = [var.operator_principal_arns]
}

# A second deployment in the same account must not collide with the first.
run "every_name_carries_the_prefix" {
  command = plan
  variables {
    name                = "alice"
    operator_account_id = "222222222222"
    operator_observe    = true
    operator_maintain   = true
    operator_recover    = true
  }
  assert {
    condition = alltrue([for n in concat(
      [
        aws_iam_role.instance.name,
        aws_iam_instance_profile.instance.name,
        aws_security_group.this.name,
        aws_kms_alias.this.name,
        aws_s3_bucket.backups.bucket_prefix,
        aws_s3_bucket.ops_output.bucket_prefix,
        aws_vpc.this.tags.Name,
        aws_subnet.this.tags.Name,
        aws_instance.this.tags.Name,
        aws_ebs_volume.data.tags.Name,
        output.ssm_prefix,
      ],
      [for r in aws_iam_role.operator : r.name],
      [for d in aws_ssm_document.op : d.name],
      [for p in aws_ssm_parameter.config : p.name],
      [for p in aws_ssm_parameter.secret : p.name],
    ) : can(regex("^(alias/|/)?alice(-|/|$)", n))])
    error_message = "every account-scoped name must start with the name variable"
  }
  assert {
    condition = one([for s in jsondecode(aws_iam_role_policy.instance.policy).Statement : s.Resource if s.Sid == "ReadOwnParameters"]) == [
      "arn:aws:ssm:eu-central-1:111111111111:parameter/alice",
      "arn:aws:ssm:eu-central-1:111111111111:parameter/alice/*",
    ]
    error_message = "the instance may read only its own deployment's parameters"
  }
}

run "default_names" {
  command = plan
  assert {
    condition     = aws_iam_role.instance.name == "memory-instance" && aws_kms_alias.this.name == "alias/memory" && output.ssm_prefix == "/memory"
    error_message = "the default name must keep the memory-* names"
  }
}

run "name_rejects_reserved_and_malformed" {
  command = plan
  variables {
    name = "aws-prod"
  }
  expect_failures = [var.name]
}
