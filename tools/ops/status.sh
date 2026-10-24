#!/usr/bin/env bash
# Report the health of this deployment as numbers and states only.
#
#   tools/ops/status.sh
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"

echo "[health]"
health_summary
echo "[containers]"
container_states
echo "[resources]"
# DISK_PATH holds the data (on EC2 its own volume); the root disk can fill separately.
df -Pk "$DISK_PATH" | awk 'NR==2 {print "disk_used_pct=" $5 "\ndisk_free_mb=" int($4 / 1024)}'
df -Pk / | awk 'NR==2 {print "root_disk_used_pct=" $5 "\nroot_disk_free_mb=" int($4 / 1024)}'
if [[ -r /proc/meminfo ]]; then
    awk '/^MemTotal:/ {t=$2} /^MemAvailable:/ {a=$2} END {print "mem_total_mb=" int(t / 1024) "\nmem_available_mb=" int(a / 1024)}' /proc/meminfo
fi
echo "[update]"
echo "last_result=$({ tail -n 200 "$BACKUP_DIR/update.log" 2>/dev/null | grep '^UPDATE RESULT' | tail -n1; } || true)"
if [[ -e $UPDATE_FAILED_MARKER ]]; then
    echo "update_failed=yes since=$(date -u -r "$UPDATE_FAILED_MARKER" +%FT%TZ)"
else
    echo "update_failed=no"
fi
echo "update_in_progress=$([[ -f $STATE ]] && echo yes || echo no)"
# Written by the nightly update job; only its first word is read.
check=$(awk 'NR==1 {print $1}' "$STATE_DIR/last-update-check" 2>/dev/null || true)
case "$check" in
    ok|failed) echo "last_update_check=$check since=$(date -u -r "$STATE_DIR/last-update-check" +%FT%TZ)" ;;
    *) echo "last_update_check=unknown" ;;
esac
if [[ -e $RESTORE_FAILED_MARKER ]]; then
    echo "restore_failed=yes since=$(date -u -r "$RESTORE_FAILED_MARKER" +%FT%TZ)"
else
    echo "restore_failed=no"
fi
echo "[backup]"
# Only the first word of the marker is read, and only ok or failed is printed.
result=$(awk 'NR==1 {print $1}' "$LAST_BACKUP_MARKER" 2>/dev/null || true)
case "$result" in
    ok|failed) echo "last_backup=$result since=$(date -u -r "$LAST_BACKUP_MARKER" +%FT%TZ)" ;;
    *) echo "last_backup=unknown" ;;
esac
shopt -s nullglob
dumps=("$BACKUP_DIR"/postgres-*.dump)
safety=("$BACKUP_DIR"/pre-restore-[0-9]*.dump)
safety_qdrant=("$BACKUP_DIR"/pre-restore-qdrant-*.tar.gz)
echo "update_dumps=${#dumps[@]} pre_restore_dumps=${#safety[@]} pre_restore_qdrant=${#safety_qdrant[@]}"
echo "[timers]"
if command -v systemctl >/dev/null; then
    for timer in memory-update.timer memory-backup.timer memory-reboot.timer; do
        enabled=$(systemctl is-enabled "$timer" 2>/dev/null | tr -cd 'a-z-' || true)
        echo "$timer=$([[ -z $enabled || $enabled == not-found ]] && echo missing || echo "$enabled")"
    done
    echo "update_service_failed=$(systemctl is-failed --quiet memory-update.service && echo yes || echo no)"
else
    echo "timers=unknown"
fi
echo "[system]"
echo "reboot_pending=$([[ -f /var/run/reboot-required ]] && echo yes || echo no)"
