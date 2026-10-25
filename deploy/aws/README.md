# Memory on EC2

One `terraform apply` stands up a memory instance in your own AWS account, on your own domain. The instance keeps its OS and the app up to date unattended. An operator you choose can repair it through a fixed set of actions without being able to read your data.

What it creates:

- a VPC with one public subnet, a security group open on 80 and 443 only, and an Elastic IP;
- an Ubuntu 24.04 instance with no key pair and IMDSv2 required (hop limit 1, so containers can't reach the metadata service), plus a separate data volume;
- one customer-managed KMS key that encrypts both volumes, the backups and the secrets;
- a private, versioned, SSE-KMS backup bucket;
- a private ops-output bucket for operator command output, which expires it after 14 days;
- SSM parameters under `/<name>/`: generated secrets as SecureStrings, and the instance's config;
- one SSM Command document per repair action, plus up to three operator roles.

Admin access is SSM Session Manager, and only for your own IAM principals. There is no SSH.

Every account-wide name starts with `name` (default `memory`): `memory-instance`, `alias/memory`, the `/memory/` parameter path, the buckets, documents and operator roles. Deployments with different names can share an account and region.

One account per person is still the recommended setup. Whoever holds an AWS account can always read what is in it: the account's admins can use the KMS key, so they can decrypt the backups, snapshots and secrets. Two people sharing an account can each read the other's data. With an account each, the only person who can read your data is you.

## Handing an account to its owner

When an operator sets up an account for someone else, the owner has to end up as the only one holding it. Do this before any real data goes in: until the last step, the operator could read anything in the account.

1. The operator creates the account in their AWS Organization.
2. Before handing it over, the operator changes the account's root email to the owner's own address.
3. The owner signs in as root with "Forgot password", sets a new password and turns on MFA for root.
4. The owner adds a payment method. The operator's card is fine: paying the bill gives no access.
5. The account leaves the organization (Organizations → Leave organization, from the owner's account). An organization's management account can otherwise get in at any time.
6. The owner deletes the `OrganizationAccountAccessRole` role in IAM. It lets the management account in, and leaving the organization doesn't remove it. Check IAM for any other user or role they don't recognise.
7. The owner runs `terraform apply` with their own credentials, setting `operator_account_id` to the operator's account and turning on the roles they want (see below). The owner keeps the Terraform state: it holds the generated secrets.
8. If the operator could have seen the secrets before the hand-over (they ran an apply, or held the state), the owner:
   - moves the Terraform state to a backend only they can read, since a refresh writes the current values into it;
   - replaces the Voyage key at Voyage, and enters the new one on the settings page. If the key came from `voyage_api_key`, they instead put the new one there (re-apply) and in `/opt/memory/secrets/voyage_key.txt` on the box, and run `restart`;
   - runs `sudo /opt/memory/tools/ops/rotate-keys.sh --yes` on the box once data matters ([Rotating keys](#rotating-keys)).

## What your operator can and can't do

The operator works from their own AWS account, through up to three roles you switch on:

- **observe**: see whether the app is healthy, and read its recent error messages.
- **maintain**: restart it, install a release, free disk space, reboot.
- **recover**: roll back an update, restore the latest backup, or rotate the keys Memory generated. The first two can lose your most recent changes, and a failed rotation can leave the app down, so leave this off unless something is broken or you want the operator to rotate keys.

The operator can't log in to the machine, read your notes, emails or files, read or decrypt the backups, see your passwords or keys (rotation makes the new ones on the box and never shows them), or change what these actions do. Only you can change them, by re-applying. Every action they run is recorded in your account's CloudTrail (in the AWS console under CloudTrail → Event history).

Two limits:

- Error messages can occasionally include fragments of text. Turn on observe only for an operator you'd trust with that.
- Whoever signs releases (`release_signers`) can ship code that reads your data. Updates wait `update_delay_hours` before installing, which gives time to notice a bad one. If you trust no one with that, set `auto_update = false` and install releases yourself after review.

## Usage

```hcl
# terraform.tfvars: this file holds a key, so keep it out of git
domain          = "memory.example.com"
voyage_api_key  = "pa-..." # optional: or enter it on the settings page
release_signers = ["ssh-ed25519 AAAA... releases@example.com"]
```

```bash
cd deploy/aws
terraform init
terraform apply
terraform output public_ip                 # point the domain's A record here
```

Set `route53_zone_id` to have the A record created for you. Caddy gets the certificate once DNS resolves.

### First login

Once bootstrap finishes, create the first account (an admin) at `https://<domain>/ui/setup`. It asks for the setup token the api logs while no account exists. Read it over Session Manager (needs the [Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)):

```bash
aws ssm start-session --region <region> --target "$(terraform output -raw instance_id)"
# in the session:
cd /opt/memory && sudo docker compose logs api | grep 'setup token' | tail -n 1
```

The api makes a new token each time it restarts, so take the last line. Once the account exists the token is gone, and older lines are useless. Neither `status` nor `logs` shows the token: the operator can run both, and their output stays in the ops-output bucket. Anyone holding the token can claim the instance, so create the account soon after the stack comes up.

The Terraform state holds every generated secret. Use a remote backend with encryption and restricted access (for example S3 with SSE-KMS), not a local file you might share.

## Inputs

| Name | Default | |
|---|---|---|
| `domain` | required | Hostname to serve |
| `name` | `memory` | Prefix for every account-wide name. Up to 20 lowercase letters, digits and dashes |
| `voyage_api_key` | `""` | Embeddings key, stored as a SecureString. Leave it out to enter it on the settings page after install. Blanking it on an existing stack deletes the SSM parameter; the box keeps its key file |
| `release_signers` | required | SSH public keys whose signed `v*` tags auto-install |
| `region` | `eu-central-1` | |
| `instance_type` | `t3.large` | 2 vCPU and 8 GB, enough for light use. Use `t3.xlarge` for heavy agent use |
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

`public_ip`, `url`, `instance_id`, `instance_arn`, `instance_role_arn`, `kms_key_arn`, `backup_bucket`, `ops_output_bucket`, `ssm_prefix`, `documents` and `document_arns` (per action), `operator_role_arns` and `operator_actions` (per enabled role).

## On the instance

The first boot takes 10–20 minutes: it installs Docker, Caddy, the AWS CLI and unattended-upgrades, builds the images and starts the stack. Follow it in `/var/log/memory-bootstrap.log` (root only; it never contains secret values). To re-run it, for example after a failure, run `sudo memory-bootstrap`. It keeps whatever is already set up. On an instance created from a release after v0.2.1, a re-run before any install has succeeded also moves the checkout to the newest signed release and rebuilds the images, so a fixed release replaces a broken one. Once installed, only `update-to` and the nightly update change the version.

### Recovering a failed first install on an older instance

Terraform ignores user-data changes, so an instance created from v0.2.1 or earlier keeps its old `memory-bootstrap`. That version doesn't move the checkout on a re-run, and it isn't protected from the snap AWS CLI problem under SSM. To move it to a fixed release, in a Session Manager shell:

0. Stop the old bootstrap from rewriting `daemon.json`: run `sudo sed -i '\|> /etc/docker/daemon.json$|d' /usr/local/sbin/memory-bootstrap`. Then follow "Moving images off the root disk" below, and skip its last step.
1. Run `sudo systemd-run --wait --pipe --collect /usr/local/sbin/memory-bootstrap`. This rewrites `/etc/memory/allowed_signers`. The install step may still fail.
2. If it failed, check out the newest signed release using the copy of `select_release.sh` that the first boot installed, which verifies the tag's signature:
   ```
   sudo -i
   cd /opt/memory && git fetch -q --tags --force origin
   tag=$(STATE_DIR=/var/lib/memory bash /usr/local/lib/memory/select_release.sh \
       --require-signed /etc/memory/allowed_signers --min-age-hours 0) && git checkout -q --detach "refs/tags/$tag"
   ```
   Exit status 3 means the checkout is already at the newest signed release.
3. Run step 1 again. The checkout's `tools/install.sh` now rebuilds the images and starts the whole stack.

### Moving images off the root disk

On Docker 29, the containerd image store keeps image layers in `/var/lib/containerd` on the 30 GB root disk, where the worker image can fill it. Instances created after v0.2.1 use the classic store under `/srv/memory/docker`. To move an older instance, run these as root in `/opt/memory`. The stack is down until the images are rebuilt, and the worker image takes a while.

1. `docker compose down`, without `-v`. The volumes are under `/srv/memory/docker` and neither store change touches them.
2. `docker system prune -af && docker builder prune -af`. Run this while the containerd store is still active, so its space is freed.
3. Set `/etc/docker/daemon.json` to `{"data-root": "/srv/memory/docker", "features": {"containerd-snapshotter": false}}`, then `systemctl restart docker`.
4. `docker compose up -d`. This rebuilds the images on the data volume.

If you already moved containerd's `root` to the data volume in `/etc/containerd/config.toml`, the layers are off the root disk already. The same steps still apply, and that containerd root then holds only container state.

Everything that must outlive the root disk is on the data volume, mounted at `/srv/memory`:

| Path | Holds |
|---|---|
| `/opt/memory` → `/srv/memory/app` | The checkout, `.env`, `secrets/`, `memory_files/`, update dumps in `backups/` |
| `/var/lib/memory` → `/srv/memory/state` | Update, restore and backup markers, first sightings of tags |
| `/srv/memory/docker` | Docker's data root: images, build cache and the Postgres, Qdrant and Redis volumes. Bootstrap turns off the containerd image store, which would keep image layers in `/var/lib/containerd` on the root disk. The nightly update prunes build cache older than a week. |

Maintenance commands using the shared system lock must run as root (use `sudo`).
Host configuration installs `/etc/tmpfiles.d/memory-lock.conf`, which creates
`/run/lock/memory-nightly.lock` with root ownership and mode 600 on every boot.
It also repairs ownership in place when configuration is reapplied, preserving
any lock already held by a running job.

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
- **Disaster recovery.** `BACKUP_ENCRYPTION_KEY` and `SECRETS_ENCRYPTION_KEY` live in SSM, as well as on the box. Restoring into a fresh data volume needs both: the first decrypts the backups, the second the secrets stored in the database. After a rotation, the values backups still need stay in SSM under `/<name>/previous/<key>/`, and `restore-latest-backup` reads them there, so a backup from before the rotation restores onto a new volume too.

## Repair actions

| Action | Role | Does |
|---|---|---|
| `status` | observe | Version, health, migrations, containers, disk, memory, last update and backup, pending reboot |
| `logs [lines]` | observe | The last error records of the api and worker containers (default 100 lines each), through a redaction filter |
| `restart [service]` | maintain | Restarts one compose service, or all of them |
| `update-to <tag>` | maintain | Runs the same signature and age checks as the nightly update, then updates; refuses a tag older than the installed one (`rollback` goes back) |
| `disk-cleanup` | maintain | Prunes old images, build cache and old dumps |
| `reboot` | maintain | Reboots in one minute |
| `rollback` | recover | Restores the pre-update dump and checks out the previous version |
| `restore-latest-backup` | recover | Restores the newest backup, after keeping a fresh dump |
| `rotate-keys [only]` | recover | Rotates the keys Memory generated (secrets encryption, Postgres, Redis, backup), or only the comma-separated names in `only`, after keeping a fresh dump; puts the old ones back if anything fails |

Every action except `status` and `logs` refuses with "busy" while another one, or a nightly job, is running.

Each document embeds `tools/ops/lib.sh` and the action's script as they were when you ran `terraform apply`. A new release can't change what a document does. When the scripts change, the next plan shows the diff and you decide whether to apply it.

A document's name ends in a hash of its content, for example `memory-status-1a2b3c4d`. A changed script makes a new document and deletes the old one, so no earlier version stays runnable. The `documents` output has the current names, and the operator roles always point at them.

You can run any of these yourself on the box, for example `sudo systemd-run --wait --pipe --collect /opt/memory/tools/ops/status.sh`. Run them, and any `aws` command that writes to a file, through `systemd-run` like this: in a Session Manager shell the snap AWS CLI can exit 0 yet leave a redirected file empty. The documents and `memory-bootstrap` do this themselves. A careful owner never needs the operator.

### Rotating keys

`rotate-keys` makes new values on the box. It writes them to the files the stack reads and to the SSM parameters, and never prints them. The instance role may overwrite only those generated parameters (not the config or `voyage_api_key`), and write parameters under `/<name>/previous/`. Those hold the backup and secrets keys that backups need, and the box never deletes them: a bucket version can outlive `backup_retention_days` by as much again. [UPDATING.md](../../UPDATING.md#rotating-keys) lists what each key affects, and has the command to delete the kept keys older than your oldest backup.

`ssm:PutParameter` can also attach a parameter policy (an expiration deletes the parameter and its history) or move it to another KMS key, and IAM can't limit that. So a compromised box could destroy these copies, as it could the backups' keys on its own disk. SSM keeps the last 100 values of each parameter (`aws ssm get-parameter-history --with-decryption`, owner only), which is the way back from a value overwritten by mistake.

Terraform sets each of these parameters once and ignores its value after that, so a re-apply doesn't undo a rotation. The box also refuses a `backup_encryption_key` in SSM that differs from the one it backs up with, so a parameter reset by an older module or a replacement can't take over its backups. It keeps its own key, still takes the rest of the config (signers included), and holds updates back (`last_update_check=failed`) until you fix the SSM value or rotate. `terraform destroy` doesn't remove the `previous/` parameters. Delete them afterwards with:

```bash
aws ssm get-parameters-by-path --path /<name>/previous --recursive --query 'Parameters[].Name' --output text \
  | xargs -n 10 aws ssm delete-parameters --names
```

The state still holds the values the last refresh read.

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
aws sts assume-role --role-arn arn:aws:iam::<owner>:role/<name>-observe --role-session-name ops
# with the returned credentials; document names come from the documents output
aws ssm send-command --region <region> --instance-ids <instance_id> \
  --document-name <name>-status-<hash> \
  --output-s3-bucket-name <ops_output_bucket> --output-s3-key-prefix ops
#   -> prints the CommandId
aws ssm send-command ... --document-name <name>-update-to-<hash> --parameters tag=v1.4.0 ...
aws s3 ls --recursive s3://<ops_output_bucket>/ops/<command id>/
aws s3 cp s3://<ops_output_bucket>/ops/<command id>/<instance id>/awsrunShellScript/<step>/stdout -
```

Output appears once the command finishes, under `s3://<bucket>/<prefix>/<command id>/<instance id>/awsrunShellScript/<step>/{stdout,stderr}`. `<step>` is the action name with `_` for `-`, such as `update_to`.

To check the roles after an apply, turn all three on and run `tests/simulate_operator_policies.sh`. It uses IAM policy simulation to confirm two things. First, each role is explicitly denied shells, `AWS-RunShellScript`, the other roles' documents, command-output APIs, `kms:Decrypt`, the backup bucket and parameter reads and writes. Second, it may read the ops-output bucket. It also checks that the instance role may write only the parameters `rotate-keys` rotates. `terraform test` runs the plan-level checks with mock providers and needs no AWS access.

## Releases and revocation

The instance installs only `v*` tags that carry a valid SSH signature from a key in `release_signers` and are at least `update_delay_hours` old. A tag's age is the later of its own date and when the box first saw it.

The `valid-before` option in allowed_signers is checked against the date the signer put in the tag, which the signer controls. It can't revoke a key. To revoke a signer, remove their key from `release_signers` and re-apply.

## Trust limits

1. **Whoever controls signed release tags can read the data.** A malicious release can exfiltrate anything. Signatures and `update_delay_hours` make that visible rather than impossible. If you want to trust no one, set `auto_update = false` and review each release before running `update-to` or `tools/update.sh`.
2. **Output can leak content.** An action's output is only as safe as what its script prints. The ops scripts print numbers, states and fixed strings, except `logs`. The app's error logs have been audited not to carry item text, search queries or keys, and `logs` filters them again: it keeps only ERROR and CRITICAL lines and the frame locations and exception types of tracebacks, and strips quoted values, URL paths and queries, emails and credential-shaped strings from what it keeps. Unquoted text in an error message gets through. A future log statement that interpolates item text would leak it, so turn `operator_observe` on only for an operator you'd trust with occasional fragments of error text, or read `tools/ops/logs.sh` before you do.
3. **The operator can harm availability, not confidentiality,** beyond what `logs` lets through (item 2). The operator sees only the output of its own documents. `restore-latest-backup` and `rollback` can destroy recent writes, and `reboot` and `restart` cause downtime. Keep `operator_recover` off unless you need it.
4. **Your own admins can read everything.** The KMS key uses the default key policy, so any principal in your account whose IAM policy allows the key can decrypt the volumes' snapshots, the backups and the secrets. Anyone who can read the Terraform state can read the secrets too.

## Destroying

The data volume has `prevent_destroy`, so neither `terraform destroy` nor a plan that would replace it can delete the live databases by accident. To remove the stack deliberately:

1. Take a final backup if you want one.
2. Delete the `prevent_destroy` block from `aws_ebs_volume.data` in `instance.tf`. Alternatively, keep the volume: run `terraform state rm aws_volume_attachment.data aws_ebs_volume.data`, then delete it later by hand.
3. Empty the backup bucket, all versions included, or `terraform destroy` fails on it. The ops-output bucket is emptied for you.
4. Run `terraform destroy`.

The KMS key is scheduled for deletion with a 30-day window. Until that window ends you can cancel the deletion, and with it you can still decrypt old snapshots.
