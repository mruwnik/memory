#!/usr/bin/env bash
# Nightly backup on the host (memory-backup.timer), into the same S3 layout the
# worker's backup task and tools/ops/restore-latest-backup.sh use:
#   databases/  Postgres and Qdrant, by tools/backup_databases.sh in the compose
#               backup service, never pruning (the bucket's lifecycle expires them)
#   <dir>.tar.gz.enc|ssl  private file directories, encrypted in a one-off worker
#   <dir>/      the other file directories, synced as they are
# Containers can't reach the instance metadata service, so the backup service
# gets the role's short-lived credentials for its run only; the uploads run on
# the host. Writes "ok" or "failed" as the first word of $STATE_DIR/last-backup.
#
# Env (ops.env, written by configure.sh): S3_BACKUP_BUCKET, S3_BACKUP_PREFIX,
#      S3_BACKUP_REGION, BACKUP_ENCRYPTION_KEY. APP_UID (default 1000);
#      CRED_MIN_SECONDS (default 3600), the credentials' lifetime a database
#      backup needs, waiting up to CRED_WAIT_SECONDS (3600) for a refresh.
set -Eeuo pipefail
# shellcheck source-path=SCRIPTDIR source=../../../tools/ops/lib.sh
source "$(dirname "$0")/../../../tools/ops/lib.sh"

: "${S3_BACKUP_BUCKET:?}" "${S3_BACKUP_PREFIX:?}" "${S3_BACKUP_REGION:?}" "${BACKUP_ENCRYPTION_KEY:?}"
export S3_BACKUP_BUCKET S3_BACKUP_PREFIX S3_BACKUP_REGION BACKUP_ENCRYPTION_KEY
APP_UID=${APP_UID:-1000}
DEST=s3://$S3_BACKUP_BUCKET/$S3_BACKUP_PREFIX

mkdir -p "$STATE_DIR"
stage=$(mktemp -d "$STATE_DIR/backup-stage.XXXXXX")
# shellcheck disable=SC2329  # called by the EXIT trap
finish() {
    local rc=$?
    rm -rf "$stage"
    echo "$( ((rc)) && echo failed || echo ok) $(date -u +%FT%TZ)" > "$LAST_BACKUP_MARKER"
}
trap finish EXIT

epoch() {
    python3 -c 'import datetime, sys; print(int(datetime.datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00")).timestamp()))' "$1"
}

# Sets creds once they last CRED_MIN_SECONDS: the upload streams for as long as
# the dump takes and can't pick up new ones. IMDS rotates them at least five
# minutes before they expire, so waiting for a refresh is bounded.
role_credentials() {
    local line expiry left deadline=$((SECONDS + ${CRED_WAIT_SECONDS:-3600}))
    while true; do
        creds=()
        expiry=""
        while IFS= read -r line; do
            case "$line" in
                AWS_ACCESS_KEY_ID=*|AWS_SECRET_ACCESS_KEY=*|AWS_SESSION_TOKEN=*) creds+=("$line") ;;
                AWS_CREDENTIAL_EXPIRATION=*) expiry=${line#*=} ;;
            esac
        done < <(aws configure export-credentials --format env-no-export)
        if ((${#creds[@]} != 3)) || [[ -z $expiry ]]; then
            echo "could not get the instance role's credentials" >&2
            return 1
        fi
        left=$(( $(epoch "$expiry") - $(date +%s) ))
        ((left >= ${CRED_MIN_SECONDS:-3600})) && return 0
        if ((SECONDS >= deadline)); then
            echo "the instance role's credentials expire in ${left}s and did not refresh" >&2
            return 1
        fi
        sleep "${CRED_POLL_SECONDS:-60}"
    done
}

backup_databases() {
    local creds
    role_credentials || return 1
    # Assignments, not arguments, so the credentials stay out of argv.
    (
        export "${creds[@]}" MAX_BACKUPS=0
        $COMPOSE --profile backup run --rm -T \
            -e AWS_ACCESS_KEY_ID -e AWS_SECRET_ACCESS_KEY -e AWS_SESSION_TOKEN \
            -e S3_BACKUP_BUCKET -e S3_BACKUP_PREFIX -e S3_BACKUP_REGION \
            -e BACKUP_ENCRYPTION_KEY -e MAX_BACKUPS backup
    )
}

# Same tarballs, ciphers and names as backup_encrypted_directory; prints the
# directories that are synced unencrypted instead.
ENCRYPT='from pathlib import Path
from memory.common import settings
from memory.workers.tasks import backup
out = Path("/out")
for d in settings.storage_dirs:
    if not d.exists():
        continue
    if d not in settings.PRIVATE_DIRS:
        print("sync", d.name)
        continue
    with backup.create_tarball_file(d) as tar:
        if tar.stat().st_size > backup.MAX_FERNET_SIZE:
            backup.encrypt_file_streaming(tar, out / f"{d.name}.tar.gz.ssl")
        else:
            (out / f"{d.name}.tar.gz.enc").write_bytes(backup.get_cipher().encrypt(tar.read_bytes()))'

backup_files() {
    local manifest kind name archive
    chown "$APP_UID:$APP_UID" "$stage" || return 1
    # TMPDIR on the stage: the worker's /tmp is a tmpfs, too small for a tarball.
    manifest=$($COMPOSE run --rm --no-deps -T -e BACKUP_ENCRYPTION_KEY -e TMPDIR=/out \
        -v "$stage:/out" --entrypoint python worker -c "$ENCRYPT") || return 1
    shopt -s nullglob
    for archive in "$stage"/*.tar.gz.enc "$stage"/*.tar.gz.ssl; do
        aws s3 cp "$archive" "$DEST/${archive##*/}" --region "$S3_BACKUP_REGION" --only-show-errors || return 1
    done
    while read -r kind name; do
        [[ $kind == sync && $name =~ ^[A-Za-z0-9._-]+$ ]] || continue
        # No --delete: the role can't delete, so a removed file stays until it expires.
        aws s3 sync "$ROOT/memory_files/$name" "$DEST/$name" --region "$S3_BACKUP_REGION" \
            --no-follow-symlinks --only-show-errors || return 1
    done <<<"$manifest"
}

failed=0
log "Backing up Postgres and Qdrant"
backup_databases || { echo "database backup failed" >&2; failed=1; }
log "Backing up files"
backup_files || { echo "file backup failed" >&2; failed=1; }
exit "$failed"
