#!/usr/bin/env bash
# Renders the host's config from SSM and this checkout: allowed_signers,
# ops.env and the Caddyfile, and the systemd units and timers in this
# directory (removing memory-* units it no longer ships), plus the drop-in
# that keeps unattended upgrades out of the nightly jobs. Bootstrap runs it, and so does the nightly update before and after
# each update, so a re-apply or a new release reaches the box. Idempotent.
#
# Env: MEMORY_SSM_PREFIX, AWS_DEFAULT_REGION; ALLOWED_SIGNERS, OPS_ENV,
#      CADDYFILE, UNIT_DIR (defaults under /etc).
set -Eeuo pipefail
umask 077

here=$(cd "$(dirname "$0")" && pwd)
ALLOWED_SIGNERS=${ALLOWED_SIGNERS:-/etc/memory/allowed_signers}
OPS_ENV=${OPS_ENV:-/etc/memory/ops.env}
CADDYFILE=${CADDYFILE:-/etc/caddy/Caddyfile}
UNIT_DIR=${UNIT_DIR:-/etc/systemd/system}

param() {
    aws ssm get-parameter --name "${MEMORY_SSM_PREFIX:?}/$1" --with-decryption \
        --query Parameter.Value --output text
}
signers=$(param release_signers)
delay=$(param update_delay_hours)
domain=$(param domain)
if [[ -z $signers || ! $delay =~ ^[0-9]+$ || ! $domain =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ ]]; then
    echo "unusable config in SSM; keeping the current one" >&2
    exit 1
fi
env_lines=()
for name in backup_encryption_key s3_backup_bucket s3_backup_prefix s3_backup_region; do
    value=$(param "$name")
    env_lines+=("$(printf '%s=%q' "$(tr '[:lower:]' '[:upper:]' <<<"$name")" "$value")")
done
# DISK_PATH is the data volume, which status and disk-cleanup measure.
env_lines+=(
    "MIN_AGE_HOURS=$delay" "MEMORY_SSM_PREFIX=$MEMORY_SSM_PREFIX"
    "AWS_DEFAULT_REGION=${AWS_DEFAULT_REGION:?}" "DISK_PATH=/srv/memory"
)

# Every value is read before anything is written, so a failed read changes nothing.
mkdir -p "$(dirname "$ALLOWED_SIGNERS")" "$(dirname "$OPS_ENV")"
printf '%s\n' "$signers" > "$ALLOWED_SIGNERS.tmp"
mv "$ALLOWED_SIGNERS.tmp" "$ALLOWED_SIGNERS"
printf '%s\n' "${env_lines[@]}" > "$OPS_ENV.tmp"
mv "$OPS_ENV.tmp" "$OPS_ENV"

caddy=$(printf '%s {\n\treverse_proxy 127.0.0.1:8000\n}\n' "$domain")
if [[ $caddy != "$(cat "$CADDYFILE" 2>/dev/null)" ]]; then
    printf '%s\n' "$caddy" > "$CADDYFILE"
    chmod 644 "$CADDYFILE"
    systemctl reload-or-restart caddy
fi

timers=()
for unit in "$here"/memory-*.service "$here"/memory-*.timer; do
    install -m 0644 "$unit" "$UNIT_DIR/"
    [[ $unit == *.timer ]] && timers+=("${unit##*/}")
done
for unit in "$UNIT_DIR"/memory-*.service "$UNIT_DIR"/memory-*.timer; do
    [[ -e $unit && ! -e $here/${unit##*/} ]] || continue
    [[ $unit == *.timer ]] && systemctl disable --now "${unit##*/}"
    rm -f "$unit"
done
mkdir -p "$UNIT_DIR/apt-daily-upgrade.service.d"
install -m 0644 "$here/apt-daily-upgrade.conf" "$UNIT_DIR/apt-daily-upgrade.service.d/memory.conf"
systemctl daemon-reload
systemctl enable --now "${timers[@]}"
