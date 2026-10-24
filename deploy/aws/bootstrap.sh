#!/bin/bash
# First-boot setup of a memory instance: user data, rendered by instance.tf.
# Keeps a copy as /usr/local/sbin/memory-bootstrap; re-running that as root
# keeps whatever is already set up. Logs to /var/log/memory-bootstrap.log,
# never secret values.
set -Eeuo pipefail
umask 022
[[ $0 -ef /usr/local/sbin/memory-bootstrap ]] || install -m 0700 "$0" /usr/local/sbin/memory-bootstrap
touch /var/log/memory-bootstrap.log
chmod 600 /var/log/memory-bootstrap.log
exec > >(tee -a /var/log/memory-bootstrap.log) 2>&1

REGION=${region}
PREFIX=${ssm_prefix}
ROOT=${install_dir}
VOLUME=${data_volume_id}
# Everything that must outlive the root disk lives on the data volume.
DATA=/srv/memory
STATE_DIR=/var/lib/memory
SIGNERS=/etc/memory/allowed_signers
export DEBIAN_FRONTEND=noninteractive

log() { echo "==> $(date -u +%FT%TZ) $*"; }
trap 'log "bootstrap FAILED at line $LINENO"' ERR
apt_get() { apt-get -y -qq -o DPkg::Lock::Timeout=600 "$@"; }
param() {
    aws ssm get-parameter --region "$REGION" --name "$PREFIX/$1" --with-decryption \
        --query Parameter.Value --output text
}

log "Base packages"
apt_get update
apt_get install ca-certificates curl gnupg git openssl unattended-upgrades
snap wait system seed.loaded
snap list aws-cli >/dev/null 2>&1 || snap install aws-cli --classic
ln -sfn /snap/bin/aws /usr/local/bin/aws

log "Data volume $VOLUME"
dev=/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_$${VOLUME//-/}
for _ in $(seq 120); do [[ -e $dev ]] && break; sleep 5; done
[[ -e $dev ]] || { log "data volume never attached"; exit 1; }
# blkid also exits 2 when it can't read the device, so prove it reads first.
dd if="$dev" of=/dev/null bs=1M count=1 status=none || { log "cannot read $dev"; exit 1; }
rc=0
blkid -p "$dev" >/dev/null || rc=$?
# 2 is "no filesystem or partition table": only then is it safe to format.
if ((rc == 2)); then
    mkfs.ext4 -q -L memory-data "$dev"
elif ((rc)); then
    log "cannot probe $dev"
    exit 1
fi
uuid=$(blkid -p -s UUID -o value "$dev")
[[ -n $uuid ]] || { log "$dev has no filesystem (a partition table?); not touching it"; exit 1; }
grep -q "^UUID=$uuid " /etc/fstab || echo "UUID=$uuid $DATA ext4 defaults,nofail 0 2" >> /etc/fstab
mkdir -p "$DATA"
mountpoint -q "$DATA" || mount "$DATA"
mkdir -p "$DATA/docker" "$DATA/app" "$DATA/state"
chmod 700 "$DATA/state"
ln -sfnT "$DATA/app" "$ROOT"
ln -sfnT "$DATA/state" "$STATE_DIR"

log "Docker"
mkdir -p /etc/docker /etc/systemd/system/docker.service.d
echo "{\"data-root\": \"$DATA/docker\"}" > /etc/docker/daemon.json
printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA" > /etc/systemd/system/docker.service.d/memory.conf
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    > /etc/apt/sources.list.d/docker.list
curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
    | gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/caddy-stable-archive-keyring.gpg] https://dl.cloudsmith.io/public/caddy/stable/deb/debian any-version main" \
    > /etc/apt/sources.list.d/caddy-stable.list
apt_get update
apt_get install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin caddy
systemctl daemon-reload
systemctl enable --now docker

log "Unattended upgrades"
# memory-reboot.timer reboots instead, in the 03:30 window with a random delay.
cat > /etc/apt/apt.conf.d/52memory-unattended-upgrades <<'EOF'
Unattended-Upgrade::Origins-Pattern { "origin=*"; };
Unattended-Upgrade::Automatic-Reboot "false";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

log "App user (uid 1000, which the containers run as)"
getent passwd 1000 >/dev/null || useradd --uid 1000 --user-group --create-home --shell /usr/sbin/nologin memory
app_user=$(getent passwd 1000 | cut -d: -f1)
# On the Ubuntu AMI uid 1000 is cloud-init's default user, with passwordless
# sudo. Nothing here needs it: admins get in through SSM's own ssm-user.
rm -f /etc/sudoers.d/90-cloud-init-users
drop_groups() {
    local group
    for group in "$@"; do
        if id -nG "$app_user" | grep -qw "$group"; then gpasswd -d "$app_user" "$group" >/dev/null; fi
    done
}
drop_groups sudo

log "Config from SSM $PREFIX"
for _ in $(seq 30); do param domain >/dev/null && break; sleep 10; done
domain=$(param domain)
delay=$(param update_delay_hours)
repo_url=$(param repo_url)
mkdir -p /etc/memory
chmod 700 /etc/memory
# Needed to pick the release; configure.sh renders the rest of the host config.
(
    umask 077
    param release_signers > "$SIGNERS.tmp"
    mv "$SIGNERS.tmp" "$SIGNERS"
)

log "Checkout"
for dir in "$ROOT" "$DATA/app"; do
    git config --system --get-all safe.directory | grep -qxF "$dir" || git config --system --add safe.directory "$dir"
done
if ! git -C "$ROOT" rev-parse -q --verify HEAD >/dev/null; then
    # No checkout yet: pick the release before any of the repo's code runs, with
    # the copy of select_release.sh embedded at apply time.
    install -m 0755 -d /usr/local/lib/memory
    cat > /usr/local/lib/memory/select_release.sh <<'MEMORY_SELECT_RELEASE'
${select_release}
MEMORY_SELECT_RELEASE
    [[ -d $ROOT/.git ]] || git init -q "$ROOT"
    git -C "$ROOT" remote get-url origin >/dev/null 2>&1 || git -C "$ROOT" remote add origin "$repo_url"
    git -C "$ROOT" fetch -q --tags --force origin
    # The first install takes the newest signed release however young.
    min_age=$delay
    [[ -e $STATE_DIR/installed ]] || min_age=0
    tag=$(cd "$ROOT" && STATE_DIR=$STATE_DIR bash /usr/local/lib/memory/select_release.sh \
        --require-signed "$SIGNERS" --min-age-hours "$min_age")
    log "Installing $tag"
    git -C "$ROOT" checkout -q --detach "refs/tags/$tag"
fi
# The code stays root's; the app user gets the top directory for .env,
# secrets/ and memory_files/.
chown "$app_user:" "$DATA/app"

if [[ -e $STATE_DIR/installed ]]; then
    # Installed before (this is a new root disk, or a re-run): .env and secrets/
    # are on the data volume. update.sh's checkouts aren't readable by the app user.
    log "Starting the installed stack"
    docker network inspect memory-api-prod >/dev/null 2>&1 || docker network create memory-api-prod >/dev/null
    (cd "$ROOT" && APP_VERSION=$(git describe --tags --always) docker compose up -d)
else
    log "Install"
    runuser -u "$app_user" -- mkdir -p -m 700 "$ROOT/secrets"
    # install.sh generates this key unless it exists; SSM keeps the copy a restore needs.
    if [[ ! -s $ROOT/secrets/secrets_encryption_key.txt ]]; then
        key=$(param secrets_encryption_key)
        # shellcheck disable=SC2016
        printf %s "$key" | runuser -u "$app_user" -- sh -c 'umask 077; cat > "$1"' sh "$ROOT/secrets/secrets_encryption_key.txt"
    fi
    (
        DOMAIN=$domain
        ADMIN_EMAIL=$(param admin_email)
        ADMIN_PASSWORD=$(param admin_password)
        VOYAGE_API_KEY=$(param voyage_api_key)
        POSTGRES_PASSWORD=$(param postgres_password)
        JWT_SECRET=$(param jwt_secret)
        REDIS_PASSWORD=$(param redis_password)
        APP_VERSION=$(git -C "$ROOT" describe --tags --always)
        # Backups run on the host (memory-backup.timer): containers can't reach the
        # instance role.
        S3_BACKUP=off
        export DOMAIN ADMIN_EMAIL ADMIN_PASSWORD VOYAGE_API_KEY POSTGRES_PASSWORD JWT_SECRET \
            REDIS_PASSWORD APP_VERSION S3_BACKUP
        cd "$ROOT"
        usermod -aG docker "$app_user"
        runuser -u "$app_user" -- bash tools/install.sh --non-interactive || { drop_groups docker lxd; exit 1; }
    )
fi
# docker and lxd are root-equivalent. Only install.sh runs docker as the app
# user; the timers and ops scripts run as root.
drop_groups docker lxd

log "Host config, Caddy for $domain and timers"
# ops.env holds the backup settings; .env never gets them, so the app's own S3
# backup task stays off.
systemctl enable --now caddy
MEMORY_SSM_PREFIX=$PREFIX AWS_DEFAULT_REGION=$REGION "$ROOT/deploy/aws/host/configure.sh"

touch "$STATE_DIR/installed"
log "Bootstrap complete"
