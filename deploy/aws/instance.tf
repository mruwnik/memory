data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"]
  }
}

resource "aws_kms_key" "this" {
  description             = "${var.name}: EBS volumes, backups and SSM secrets"
  enable_key_rotation     = true
  deletion_window_in_days = 30
}

# tools/ops/rotate-keys.sh encrypts with alias/<the SSM prefix without its slash>.
resource "aws_kms_alias" "this" {
  name          = "alias/${var.name}"
  target_key_id = aws_kms_key.this.key_id
}

# Kept apart from the root volume so the data outlives instance replacement.
resource "aws_ebs_volume" "data" {
  availability_zone = aws_subnet.this.availability_zone
  size              = var.disk_gb
  type              = "gp3"
  encrypted         = true
  kms_key_id        = aws_kms_key.this.arn
  tags              = { Name = "${var.name}-data" }

  # Holds the live databases; see README "Destroying" to remove it on purpose.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_instance" "this" {
  ami                  = data.aws_ami.ubuntu.id
  instance_type        = var.instance_type
  iam_instance_profile = aws_iam_instance_profile.instance.name

  primary_network_interface {
    network_interface_id = aws_network_interface.this.id
  }

  # Hop limit 1 keeps containers, one hop further away, off the metadata service.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
    kms_key_id  = aws_kms_key.this.arn
  }

  # Gzipped to stay under the 16 KB user data limit. select_release.sh is
  # embedded so the first release is chosen before any of the repo's code runs.
  user_data_base64 = base64gzip(templatefile("${path.module}/bootstrap.sh", {
    region         = var.region
    ssm_prefix     = local.ssm_prefix
    install_dir    = local.install_dir
    data_volume_id = aws_ebs_volume.data.id
    select_release = file("${path.module}/../../tools/select_release.sh")
  }))

  # The OS updates itself, and bootstrap only runs on first boot: a new AMI or
  # user data must not replace a running instance. Config changes reach the box
  # through the SSM parameters instead.
  lifecycle {
    ignore_changes = [ami, user_data_base64]
  }

  tags = { Name = var.name }

  depends_on = [aws_ssm_parameter.config, aws_ssm_parameter.secret, aws_ssm_parameter.voyage_api_key, aws_route_table_association.this]
}

resource "aws_volume_attachment" "data" {
  device_name                    = "/dev/sdf"
  volume_id                      = aws_ebs_volume.data.id
  instance_id                    = aws_instance.this.id
  stop_instance_before_detaching = true
}

resource "aws_iam_role" "instance" {
  name = "${var.name}-instance"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "instance_ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:${local.partition}:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "instance" {
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # AmazonSSMManagedInstanceCore already grants GetParameter(s) on *; this
      # adds ByPath, which IAM checks against the path itself. The secrets stay
      # limited to this stack by the KMS grant below.
      {
        Sid    = "ReadOwnParameters"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"]
        Resource = [
          "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}",
          "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}/*",
        ]
      },
      {
        Sid      = "UseOwnKey"
        Effect   = "Allow"
        Action   = ["kms:Decrypt", "kms:Encrypt", "kms:GenerateDataKey"]
        Resource = aws_kms_key.this.arn
      },
      # rotate-keys.sh replaces the generated secrets and keeps the values
      # backups need under previous/; config and voyage_api_key stay the owner's.
      {
        Sid    = "RotateOwnKeys"
        Effect = "Allow"
        Action = "ssm:PutParameter"
        Resource = concat(
          [for k in local.rotated_secrets : "arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}/${k}"],
          ["arn:${local.partition}:ssm:${var.region}:${local.account_id}:parameter${local.ssm_prefix}/previous/*"],
        )
      },
      # No delete: versioning plus the lifecycle rule handle retention, so a
      # compromised instance can overwrite backups but not destroy their history.
      {
        Sid      = "WriteBackups"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.backups.arn}/*"
      },
      # Run Command output keys always contain this segment and backups never
      # do, so an operator can't aim its output at the backup bucket.
      {
        Sid      = "DenyCommandOutputInBackups"
        Effect   = "Deny"
        Action   = "s3:PutObject"
        Resource = "${aws_s3_bucket.backups.arn}/*/awsrunShellScript/*"
      },
      {
        Sid      = "ListBackups"
        Effect   = "Allow"
        Action   = "s3:ListBucket"
        Resource = aws_s3_bucket.backups.arn
      },
      # The SSM agent uploads command output here as the instance.
      {
        Sid      = "WriteCommandOutput"
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:PutObjectAcl"]
        Resource = "${aws_s3_bucket.ops_output.arn}/*"
      },
      {
        Sid      = "CheckCommandOutputBucket"
        Effect   = "Allow"
        Action   = "s3:GetEncryptionConfiguration"
        Resource = aws_s3_bucket.ops_output.arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name}-instance"
  role = aws_iam_role.instance.name
}
