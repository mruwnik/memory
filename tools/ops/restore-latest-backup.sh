#!/usr/bin/env bash
# Replace the data with the newest complete backup set: Postgres and the Qdrant
# snapshot (backup_databases.sh) and the file-storage directories (the worker's
# S3 backup). Everything written since the backup is lost. After stopping the
# app services it keeps a fresh dump as $BACKUP_DIR/pre-restore-<timestamp>.dump
# and a tarball of the Qdrant volume as pre-restore-qdrant-<timestamp>.tar.gz.
# Refuses if the newest Postgres backup has no Qdrant snapshot of the same date.
#
#   tools/ops/restore-latest-backup.sh --yes [--dry-run]
#
# Reads s3://$S3_BACKUP_BUCKET/$S3_BACKUP_PREFIX with the aws cli (the instance
# role), region $S3_BACKUP_REGION, or a local directory with the same layout
# when BACKUP_SOURCE is set. BACKUP_ENCRYPTION_KEY (or BACKUP_ENCRYPTION_KEY_FILE)
# comes from the environment or .env; a backup taken before a key rotation is
# read with a superseded key (lib.sh previous_keys), and its stored secrets are
# moved to the current SECRETS_ENCRYPTION_KEY. Qdrant is restored first, then
# Postgres, then files, so a Qdrant failure leaves Postgres untouched.
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
require_yes
take_lock

SOURCE=${BACKUP_SOURCE:-}
FILES_DIR=$ROOT/memory_files
APP_UID=${APP_UID:-1000}

# A value from the environment, else from .env with surrounding quotes removed.
config_value() {
    local value=${!1:-}
    [[ -n $value ]] || value=$(sed -n "s/^$1=//p" .env 2>/dev/null | tail -n1 | sed -E "s/^['\"]//; s/['\"]\$//")
    echo "$value"
}
BACKUP_ENCRYPTION_KEY=$(config_value BACKUP_ENCRYPTION_KEY)
if [[ -z $BACKUP_ENCRYPTION_KEY ]]; then
    key_file=$(config_value BACKUP_ENCRYPTION_KEY_FILE)
    [[ -n $key_file && -r $key_file ]] && BACKUP_ENCRYPTION_KEY=$(head -n1 "$key_file")
fi
: "${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY not set}"
# Every key a backup inside the retention can be under, one per line; never printed.
BACKUP_KEYS=$({ echo "$BACKUP_ENCRYPTION_KEY"; previous_keys backup_encryption_key; } | awk 'NF && !seen[$0]++')
export BACKUP_KEYS
BUCKET=$(config_value S3_BACKUP_BUCKET)
PREFIX=$(config_value S3_BACKUP_PREFIX)
PREFIX=${PREFIX:-memory}
REGION=$(config_value S3_BACKUP_REGION)
REGION=${REGION:-eu-central-1}
[[ -n $SOURCE || -n $BUCKET ]] || { echo "S3_BACKUP_BUCKET not set and no BACKUP_SOURCE given" >&2; exit 1; }

# Directories print with a trailing slash, like `aws s3 ls`.
src_ls() {
    if [[ -n $SOURCE ]]; then
        ls -1p "$SOURCE/$1"
        return
    fi
    aws s3 ls "s3://$BUCKET/$PREFIX/$1" --region "$REGION" | awk '$1 == "PRE" {print $2; next} {print $4}'
}
src_cat() {
    if [[ -n $SOURCE ]]; then
        cat "$SOURCE/$1"
        return
    fi
    aws s3 cp "s3://$BUCKET/$PREFIX/$1" - --region "$REGION"
}
src_sync() {
    mkdir -p "$2"
    if [[ -n $SOURCE ]]; then
        cp -a "$SOURCE/$1/." "$2/"
        return
    fi
    aws s3 sync "s3://$BUCKET/$PREFIX/$1" "$2" --region "$REGION"
}
decrypt() { openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_ENCRYPTION_KEY 2>/dev/null; }
# Fetches $1 once, then decrypts it into $2 with the first backup key whose
# result $3 accepts: a wrong key only shows at the end of the stream.
fetch_decrypted() {
    local key
    src_cat "$1" > "$2.enc" || return 1
    while read -r -u 3 key; do
        BACKUP_ENCRYPTION_KEY=$key decrypt < "$2.enc" > "$2" && "$3" "$2" && { rm -f "$2.enc"; return; }
    done 3<<<"$BACKUP_KEYS"
    return 1
}
is_gzip() { gzip -t "$1" 2>/dev/null; }
is_tar() { tar -tf "$1" > /dev/null 2>&1; }

mkdir -p "$STATE_DIR"
databases=$(src_ls databases/) || { echo "could not list the backup source" >&2; exit 1; }
latest=$(sed -En 's/^(postgres|qdrant)-([0-9]{8})\.(sql\.gz|snapshot)\.enc$/\2/p' <<<"$databases" | sort | tail -n1)
[[ -n $latest ]] || { echo "no database backups found" >&2; exit 1; }
for name in "postgres-$latest.sql.gz.enc" "qdrant-$latest.snapshot.enc"; do
    grep -qx "$name" <<<"$databases" \
        || { echo "incomplete backup set for $latest: $name is missing; refusing to restore" >&2; exit 1; }
done
top=$(src_ls "")
file_archives=$(grep -E '\.tar\.gz\.(enc|ssl)$' <<<"$top" || true)
file_dirs=$(grep -E '/$' <<<"$top" | grep -vx 'databases/' || true)

log "Restoring the backup set of $latest"
echo "postgres-$latest.sql.gz.enc qdrant-$latest.snapshot.enc"
echo "files: $(tr '\n' ' ' <<<"$file_archives $file_dirs")"
[[ $DRY_RUN == 1 ]] && exit 0

# Everything is downloaded and checked before anything is stopped or dropped.
stage=$(mktemp -d "$STATE_DIR/restore-tmp.XXXXXX")
trap 'rm -rf "$stage"' EXIT
stage_failed() {
    echo "could not fetch and decrypt $1 (free_mb=$(( $(free_bytes) / 1048576 )) in $STATE_DIR); no known BACKUP_ENCRYPTION_KEY, corrupt object or full disk?" >&2
    exit 1
}
fetch_decrypted "databases/postgres-$latest.sql.gz.enc" "$stage/postgres.sql.gz" is_gzip \
    || stage_failed "postgres-$latest.sql.gz.enc"
fetch_decrypted "databases/qdrant-$latest.snapshot.enc" "$stage/qdrant.snapshot" is_tar \
    || stage_failed "qdrant-$latest.snapshot.enc"

FERNET='import io, os, sys, tarfile
from cryptography.fernet import MultiFernet
from memory.workers.tasks.backup import get_cipher
cipher = MultiFernet([get_cipher(key) for key in os.environ["BACKUP_KEYS"].split()])
data = cipher.decrypt(sys.stdin.buffer.read())
tarfile.open(fileobj=io.BytesIO(data), mode="r:gz").extractall("/app/memory_files", filter="data")'

# A wrong key yields no gzip stream, so tar fails before extracting anything.
extract_archive() {
    local key archive=$stage/archive
    src_cat "$1" > "$archive" || return 1
    while read -r -u 3 key; do
        BACKUP_ENCRYPTION_KEY=$key decrypt < "$archive" | tar -xz -C "$FILES_DIR" 2>>"$RESTORE_LOG" \
            && { rm -f "$archive"; return; }
    done 3<<<"$BACKUP_KEYS"
    return 1
}

restore_files() {
    local name dir restored=()
    mkdir -p "$FILES_DIR"
    for name in $file_archives; do
        case "$name" in
            # The worker extracts as the app user already.
            *.enc) src_cat "$name" | $COMPOSE run --rm --no-deps -T -e BACKUP_KEYS --entrypoint python worker -c "$FERNET" 2>>"$RESTORE_LOG" ;;
            *)
                extract_archive "$name"
                restored+=("$FILES_DIR/${name%%.tar.gz.*}") ;;
        esac
    done
    for dir in $file_dirs; do
        src_sync "${dir%/}" "$FILES_DIR/${dir%/}" >/dev/null 2>>"$RESTORE_LOG"
        restored+=("$FILES_DIR/${dir%/}")
    done
    # Run as root: the app (uid $APP_UID) must own and be able to write what it reads.
    for dir in ${restored[@]+"${restored[@]}"}; do
        chmod -R u=rwX,go=rX "$dir"
        chown -R "$APP_UID:$APP_UID" "$dir"
    done
}

stamp=$(date +%Y%m%d-%H%M%S)
safety=$BACKUP_DIR/pre-restore-$stamp.dump
safety_qdrant=$BACKUP_DIR/pre-restore-qdrant-$stamp.tar.gz
arm_restore_failure_trap "$safety" "$safety_qdrant"
RESTORE_STEP=preparing
log "Stopping app services"
run $COMPOSE stop "${APP_SERVICES[@]}"
wait_for_postgres
log "Keeping a fresh dump at $safety"
mkdir -p "$BACKUP_DIR"
$COMPOSE exec -T postgres pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$safety.partial"
[[ -s $safety.partial ]] || { rm -f "$safety.partial"; echo "fresh dump is empty; not restoring" >&2; false; }
mv "$safety.partial" "$safety"

RESTORE_STEP=qdrant
log "Restoring Qdrant (the current data is tarred to $safety_qdrant first)"
restore_qdrant "$stage/qdrant.snapshot" "$safety_qdrant"
RESTORE_STEP=postgres
log "Restoring Postgres"
restore_postgres "$stage/postgres.sql.gz"
rekey_restored
RESTORE_STEP=files
log "Restoring files"
restore_files
RESTORE_STEP=starting
run $COMPOSE up -d
wait_healthy > /dev/null
rm -f "$RESTORE_FAILED_MARKER"
log "Restored backup set $latest; previous state kept in $safety and $safety_qdrant"
