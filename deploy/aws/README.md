# Memory on EC2

One `terraform apply` stands up a memory instance in your own AWS account, on your own domain. The instance keeps its OS and the app up to date unattended. An operator you choose can repair it through a fixed set of actions without being able to read your data.

What it creates:

- a VPC with one public subnet, a security group open on 80 and 443 only, and an Elastic IP;
- an Ubuntu 24.04 instance with no key pair and IMDSv2 required (hop limit 1, so containers can't reach the metadata service), plus a separate data volume;
- one customer-managed KMS key that encrypts both volumes, the backups and the secrets;
- a private, versioned, SSE-KMS backup bucket;
- a private ops-output bucket for operator command output, which expires it after 14 days;
- SSM parameters under `/memory/`: generated secrets as SecureStrings, and the instance's config;
- one SSM Command document per repair action, plus up to three operator roles.

Admin access is SSM Session Manager, and only for your own IAM principals. There is no SSH.

A dedicated AWS account for memory is cleanest. Nothing else then shares the KMS key's default policy, the Run Command history or CloudTrail with it. Resource names are fixed (`memory-instance`, `alias/memory`, the `/memory/` parameter prefix and so on), so there can be one stack per account and region.

## Usage

```hcl
# terraform.tfvars: this file holds a key, so keep it out of git
domain          = "memory.example.com"
admin_email     = "you@example.com"
voyage_api_key  = "pa-..."
release_signers = ["ssh-ed25519 AAAA... releases@example.com"]
```

```bash
cd deploy/aws
terraform init
terraform apply
terraform output public_ip                 # point the domain's A record here
terraform output -raw admin_password       # first login, as admin_email
```

Set `route53_zone_id` to have the A record created for you. Caddy gets the certificate once DNS resolves.

The Terraform state holds every generated secret. Use a remote backend with encryption and restricted access (for example S3 with SSE-KMS), not a local file you might share.

## Inputs

| Name | Default | |
|---|---|---|
| `domain` | required | Hostname to serve |
| `admin_email` | required | First user |
| `voyage_api_key` | required | Embeddings key, stored as a SecureString |
| `release_signers` | required | SSH public keys whose signed `v*` tags auto-install |
| `region` | `eu-central-1` | |
| `instance_type` | `t3.large` | 2 vCPU and 8 GB, enough for light use. Use `t3.xlarge` for heavy Claude-session or agent use |
| `disk_gb` | `100` | Data volume |
| `availability_zone` | `null` | AZ for the subnet and data volume. Null takes the first available AZ at creation. Later changes are ignored, because moving AZ would replace the data volume |
| `route53_zone_id` | `null` | Create the A record in this zone |
| `repo_url` | the public memory repo | Where the instance installs and updates from |
| `auto_update` | `true` | Install new signed releases nightly |
| `update_delay_hours` | `48` | Minimum age of a tag before it auto-installs |
| `backup_retention_days` | `30` | Days a backup stays current, then the same again as a noncurrent version. Each backup is kept about twice this long, and so is any version the instance overwrites |
| `operator_account_id` | `null` | Account allowed to assume the operator roles |
| `operator_principal_arns` | `null` | Principals in that account allowed to assume the roles. Null trusts the account root, leaving the choice to that account's IAM |
| `operator_external_id` | `null` | Require this `sts:ExternalId` to assume the roles |
| `operator_observe` / `operator_maintain` / `operator_recover` | `false` | Turn each operator role on |

The instance reads its config from SSM. Changes to `release_signers`, `auto_update` and `update_delay_hours` take effect at the next update check, without replacing the instance. Changing the AMI or user data never replaces a running instance: the OS updates itself.

## Outputs

`public_ip`, `url`, `admin_email`, `admin_password` (sensitive), `instance_id`, `instance_arn`, `instance_role_arn`, `kms_key_arn`, `backup_bucket`, `ops_output_bucket`, `ssm_prefix`, `documents` and `document_arns` (per action), `operator_role_arns` and `operator_actions` (per enabled role).

## On the instance

The first boot takes 10–20 minutes: it installs Docker, Caddy, the AWS CLI and unattended-upgrades, builds the images and starts the stack. Follow it in `/var/log/memory-bootstrap.log` (root only; it never contains secret values). To re-run it, for example after a failure, run `sudo memory-bootstrap`. It keeps whatever is already set up.

Everything that must outlive the root disk is on the data volume, mounted at `/srv/memory`:

| Path | Holds |
|---|---|
| `/opt/memory` → `/srv/memory/app` | The checkout, `.env`, `secrets/`, `memory_files/`, update dumps in `backups/` |
| `/var/lib/memory` → `/srv/memory/state` | Update, restore and backup markers, first sightings of tags |
| `/srv/memory/docker` | Docker's data root, including the Postgres, Qdrant and Redis volumes |

`/etc/memory/` holds `allowed_signers` and `ops.env` (the backup key and bucket, read by the ops scripts and the nightly jobs), mode 600. `deploy/aws/host/configure.sh` renders them from SSM, along with the Caddyfile and the systemd units and timers from the checkout. Bootstrap runs it, and so does every nightly update, before and after updating. A re-apply or a new release therefore reaches the box without a new instance. Containers run as uid 1000, the instance's first user, which owns `.env`, `secrets/` and `memory_files/`. The code itself belongs to root. Bootstrap removes cloud-init's passwordless sudo for that user and, after the install, its docker and lxd groups (both root-equivalent). Admins get in through Session Manager's own `ssm-user`.

The first install takes the newest signed release, however young. After that, a release has to be `update_delay_hours` old. A replacement instance on the same data volume starts the stack it finds.

Nightly jobs, in instance time (UTC), each with up to 30 minutes of random delay and never two at once:

| Time | Unit | Does |
|---|---|---|
| 01:30 | `memory-backup` | Backs up Postgres, Qdrant and the files to the backup bucket |
| 03:30 | `memory-reboot` | Reboots if an upgrade needs it (`/run/reboot-required`) |
| 04:30 | `memory-update` | Re-renders the host config from SSM, installs the newest qualifying release, then re-renders again from the new release |

Their output is in the journal, for example `journalctl -u memory-update`. `status` reports the last update and backup results.

- **OS updates.** unattended-upgrades installs updates from every configured origin (Ubuntu, Docker and Caddy). The reboot window above replaces its own automatic reboot.
- **App updates.** The update job refreshes `allowed_signers` and the delay in `ops.env` on every run, so `update-to` uses them too, even with `auto_update` off. A changed `domain` reaches the Caddyfile the same way, but the app's `SERVER_URL` in `.env` keeps the domain it was installed with. A night with nothing to install leaves no update log. A tag that fails to install goes into `/var/lib/memory/skip-tags`. The job doesn't retry it, but still installs a newer release. `update-to` can install it explicitly. Each run writes `last-update-check` (ok or failed), which `status` reports along with the timers and a failed `memory-update` unit. Unattended upgrades wait for the nightly jobs and block them in turn, because an upgrade can restart dockerd.
- **Backups.** Containers can't reach the instance role, so the backups run on the host. Its database dumps go through the compose `backup` service, with the role's short-lived credentials for that run only. Those are the instance role's full credentials, so that one container can do whatever the role can: read the SSM secrets, use the KMS key, and read and write the backup bucket. The job waits until the credentials have at least an hour left, so an upload can't outlive them. Private file directories are encrypted in a one-off worker container and uploaded by the host. The app's own S3 backup task stays off: `.env` has no backup bucket. The instance role can't delete, so nothing is pruned: the bucket's lifecycle expires old backups. A file deleted from a synced, unencrypted directory (ebooks, comics and the like) stays in the backup until it expires, and a restore brings it back.
- **Disaster recovery.** `BACKUP_ENCRYPTION_KEY` and `SECRETS_ENCRYPTION_KEY` live in SSM, as well as on the box. Restoring into a fresh data volume needs both: the first decrypts the backups, the second the secrets stored in the database.

## Repair actions

| Action | Role | Does |
|---|---|---|
| `status` | observe | Version, health, migrations, containers, disk, memory, last update and backup, pending reboot |
| `restart [service]` | maintain | Restarts one compose service, or all of them |
| `update-to <tag>` | maintain | Runs the same signature and age checks as the nightly update, then updates |
| `disk-cleanup` | maintain | Prunes old images, build cache and old dumps |
| `reboot` | maintain | Reboots in one minute |
| `rollback` | recover | Restores the pre-update dump and checks out the previous version |
| `restore-latest-backup` | recover | Restores the newest backup, after keeping a fresh dump |

Each document embeds `tools/ops/lib.sh` and the action's script as they were when you ran `terraform apply`. A new release can't change what a document does. When the scripts change, the next plan shows the diff and you decide whether to apply it.

A document's name ends in a hash of its content, for example `memory-status-1a2b3c4d`. A changed script makes a new document and deletes the old one, so no earlier version stays runnable. The `documents` output has the current names, and the operator roles always point at them.

You can run any of these yourself on the box, for example `sudo /opt/memory/tools/ops/status.sh`. A careful owner never needs the operator.

## Operator access

Each role is assumable only from `operator_account_id`: by its root, which leaves the choice to that account's IAM, or by `operator_principal_arns` if you set it. Set `operator_external_id` as well to require an `sts:ExternalId`. Each role is switched on by its own variable. A usual setup:

- observe on permanently;
- maintain on if you trust the operator with updates;
- recover on only during an incident, then off again.

Each role may only:

- `ssm:SendCommand` with its own documents, to this instance;
- read objects in the ops-output bucket (`s3:GetObject`, `s3:ListBucket`);
- `ec2:DescribeInstances`;
- reboot this instance (maintain only).

Everything else is an explicit deny on `NotAction`, plus denies on any other document, instance or bucket. A broader policy attached to the role later can't widen it. The role can't open a session, run `AWS-RunShellScript`, change documents or parameters, read parameters, use KMS or IAM, or read the backup bucket.

The role also has no `ssm:GetCommandInvocation`, `ListCommandInvocations` or `ListCommands`. Those can't be scoped to a document or instance, so they would show the output and parameters of every Run Command in your account and region, including your own. Instead, the operator asks for output in the ops-output bucket and reads it there.

IAM has no condition key that could force SendCommand to name that bucket, but leaving it out gains nothing:

- without an output bucket, the operator just can't see the output;
- the instance role can write output only to the ops-output bucket: its one other bucket, the backups, denies any key containing `awsrunShellScript`, which every Run Command output key has;
- only the operator documents' output lands there unless you send your own output there too. Don't point your own Run Command output, Session Manager session logging or State Manager association output at that bucket.

CloudTrail in your account records every invocation.

The operator, from their own account:

```bash
aws sts assume-role --role-arn arn:aws:iam::<owner>:role/memory-observe --role-session-name ops
# with the returned credentials; document names come from the documents output
aws ssm send-command --region <region> --instance-ids <instance_id> \
  --document-name memory-status-<hash> \
  --output-s3-bucket-name <ops_output_bucket> --output-s3-key-prefix ops
#   -> prints the CommandId
aws ssm send-command ... --document-name memory-update-to-<hash> --parameters tag=v1.4.0 ...
aws s3 ls --recursive s3://<ops_output_bucket>/ops/<command id>/
aws s3 cp s3://<ops_output_bucket>/ops/<command id>/<instance id>/awsrunShellScript/<step>/stdout -
```

Output appears once the command finishes, under `s3://<bucket>/<prefix>/<command id>/<instance id>/awsrunShellScript/<step>/{stdout,stderr}`. `<step>` is the action name with `_` for `-`, such as `update_to`.

To check the roles after an apply, turn all three on and run `tests/simulate_operator_policies.sh`. It uses IAM policy simulation to confirm two things. First, each role is explicitly denied shells, `AWS-RunShellScript`, the other roles' documents, command-output APIs, `kms:Decrypt`, the backup bucket and parameter reads and writes. Second, it may read the ops-output bucket. `terraform test` runs the plan-level checks with mock providers and needs no AWS access.

## Releases and revocation

The instance installs only `v*` tags that carry a valid SSH signature from a key in `release_signers` and are at least `update_delay_hours` old. A tag's age is the later of its own date and when the box first saw it.

The `valid-before` option in allowed_signers is checked against the date the signer put in the tag, which the signer controls. It can't revoke a key. To revoke a signer, remove their key from `release_signers` and re-apply.

## Trust limits

1. **Whoever controls signed release tags can read the data.** A malicious release can exfiltrate anything. Signatures and `update_delay_hours` make that visible rather than impossible. If you want to trust no one, set `auto_update = false` and review each release before running `update-to` or `tools/update.sh`.
2. **Output can leak content.** An action's output is only as safe as what its script prints. The ops scripts print numbers, states and fixed strings. A `logs` action stays off the operator's list until the app's error logs have been audited for item text, queries and keys.
3. **The operator can harm availability, not confidentiality.** The operator sees only the output of its own documents. `restore-latest-backup` and `rollback` can destroy recent writes, and `reboot` and `restart` cause downtime. Keep `operator_recover` off unless you need it.
4. **Your own admins can read everything.** The KMS key uses the default key policy, so any principal in your account whose IAM policy allows the key can decrypt the volumes' snapshots, the backups and the secrets. Anyone who can read the Terraform state can read the secrets too.

## Destroying

The data volume has `prevent_destroy`, so neither `terraform destroy` nor a plan that would replace it can delete the live databases by accident. To remove the stack deliberately:

1. Take a final backup if you want one.
2. Delete the `prevent_destroy` block from `aws_ebs_volume.data` in `instance.tf`. Alternatively, keep the volume: run `terraform state rm aws_volume_attachment.data aws_ebs_volume.data`, then delete it later by hand.
3. Empty the backup bucket, all versions included, or `terraform destroy` fails on it. The ops-output bucket is emptied for you.
4. Run `terraform destroy`.

The KMS key is scheduled for deletion with a 30-day window. Until that window ends you can cancel the deletion, and with it you can still decrypt old snapshots.
