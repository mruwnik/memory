# shellcheck shell=bash disable=SC2034
# Shared by tools/ops/*.sh and tools/update.sh: source it, don't run it. The
# SSM documents embed this file followed by one script body, so a script only
# sources it when these functions are not defined yet.
#
# Env: COMPOSE, DOCKER, BACKUP_DIR, KEEP_BACKUPS, HEALTH_TIMEOUT as in
#      tools/update.sh; STATE_DIR (default /var/lib/memory) holds on-box state;
#      MEMORY_ROOT is the checkout when this file is embedded outside one
#      (default /opt/memory; ignored inside a checkout); OPS_ENV (default /etc/memory/ops.env) is sourced first;
#      LOCK_FILE (default /run/lock/memory-nightly.lock), see take_lock.
#
# The ops scripts run through SSM documents whose output the operator can
# read, so anything they print must be a number, a state or a fixed string:
# never command output that can carry rows, queries or secrets.
set -Eeuo pipefail
umask 077

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
if [[ -f $here/../update.sh ]]; then
    MEMORY_ROOT=$(cd "$here/../.." && pwd)
else
    MEMORY_ROOT=${MEMORY_ROOT:-/opt/memory}
fi
ROOT=$MEMORY_ROOT
cd "$ROOT"

OPS_ENV=${OPS_ENV:-/etc/memory/ops.env}
if [[ -r $OPS_ENV ]]; then
    set -a
    # shellcheck source=/dev/null
    source "$OPS_ENV"
    set +a
fi

COMPOSE=${COMPOSE:-docker compose}
DOCKER=${DOCKER:-$([[ $COMPOSE == podman* ]] && echo podman || echo docker)}
BACKUP_DIR=${BACKUP_DIR:-backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-5}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
HEALTH_POLL=${HEALTH_POLL:-5}
DB_USER=${DB_USER:-kb}
DB_NAME=${DB_NAME:-kb}
DISK_PATH=${DISK_PATH:-/}
APP_SERVICES=(api worker agents-worker slack-sync-worker ingest-hub)
STATE=$BACKUP_DIR/update-in-progress
LAST_GOOD=$BACKUP_DIR/last-good
STATE_DIR=${STATE_DIR:-/var/lib/memory}
# Paths under STATE_DIR; re-derived by the restore functions so a STATE_DIR set
# after sourcing is honoured.
state_paths() {
    # Written by tools/update.sh when an update fails; cleared by a later success.
    UPDATE_FAILED_MARKER=$STATE_DIR/update-failed
    # Tags automatic selection must not pick again (rolled back from).
    SKIP_TAGS=$STATE_DIR/skip-tags
    # Written by restore-latest-backup.sh and rollback.sh when they fail part-way.
    RESTORE_FAILED_MARKER=$STATE_DIR/restore-failed
    RESTORE_LOG=$STATE_DIR/restore.log
    # Written by rotate-keys.sh: "started", or "failed" and the keys left new or unsettled.
    ROTATE_MARKER=$STATE_DIR/rotate-keys
    # Written by the nightly backup timer: "ok" or "failed" as the first word.
    LAST_BACKUP_MARKER=$STATE_DIR/last-backup
}
state_paths

for n in KEEP_BACKUPS HEALTH_TIMEOUT; do
    if ! [[ ${!n} =~ ^[1-9][0-9]*$ ]]; then
        echo "$n must be a positive integer, got '${!n}'" >&2
        exit 2
    fi
done

DRY_RUN=0
YES=0
POSITIONAL=()
parse_args() {
    local arg
    for arg in "$@"; do
        case "$arg" in
            --dry-run) DRY_RUN=1 ;;
            --yes) YES=1 ;;
            -h|--help) sed -n '2,/^set /p' "$0" | sed '$d'; exit 0 ;;
            -*) echo "unknown option: $arg" >&2; exit 2 ;;
            *) POSITIONAL+=("$arg") ;;
        esac
    done
}

# Dry runs need no confirmation: they change nothing.
require_yes() {
    [[ $YES == 1 || $DRY_RUN == 1 ]] && return
    echo "$(basename "$0") is destructive; pass --yes to confirm (or --dry-run to preview)" >&2
    exit 2
}

# The nightly units hold the same lock through flock(1), so a mutating script
# never runs alongside an update, backup, restore, reboot or apt upgrade.
# MEMORY_LOCK_HELD=1, set by such a unit or exported here for the scripts this
# one runs, keeps them from refusing a lock their caller holds.
LOCK_FILE=${LOCK_FILE:-/run/lock/memory-nightly.lock}
take_lock() {
    [[ $DRY_RUN == 1 || ${MEMORY_LOCK_HELD:-} == 1 ]] && return
    # Hosts without flock(1) or /run/lock (macOS) run no timers to contend with.
    command -v flock > /dev/null && [[ -d ${LOCK_FILE%/*} ]] || return 0
    # Only root may create the shared system lock. A user-owned file in the
    # sticky /run/lock directory can block root's O_CREAT under protected_regular.
    if [[ $LOCK_FILE == /run/lock/memory-nightly.lock && $EUID != 0 ]]; then
        echo "system maintenance requires root; run this command with sudo" >&2
        exit 1
    fi
    exec 9>>"$LOCK_FILE"
    flock -n 9 || { echo "busy: an update, backup, restore or reboot is running; try again later" >&2; exit 1; }
    export MEMORY_LOCK_HELD=1
}

log() { echo "==> $*"; }
run() {
    echo "+ $*"
    [[ $DRY_RUN == 1 ]] || "$@"
}

free_bytes() { df -Pk "$DISK_PATH" | awk 'NR==2 {print $4 * 1024}'; }

unskip_tag() {
    [[ -f $SKIP_TAGS ]] || return 0
    grep -vxF -- "$1" "$SKIP_TAGS" > "$SKIP_TAGS.tmp" || true
    mv "$SKIP_TAGS.tmp" "$SKIP_TAGS"
}

wait_for_postgres() {
    run $COMPOSE up -d --no-recreate postgres
    [[ $DRY_RUN == 1 ]] && return
    for _ in {1..30}; do
        $COMPOSE exec -T postgres pg_isready -q -U "$DB_USER" && return
        sleep 2
    done
    echo "Postgres did not become ready" >&2
    return 1
}

# Only the four named keys, with a restricted alphabet, ever leave the box. A
# degraded API answers 503 with the same JSON body, which still counts.
health_summary() {
    local py='import json, urllib.error, urllib.request as u
try:
    d = json.load(u.urlopen("http://localhost:8000/health", timeout=5))
except urllib.error.HTTPError as e:
    d = json.load(e)
for k in ("status", "version", "db_revision", "expected_revision"):
    print(f"{k}={d.get(k)}")'
    { $COMPOSE exec -T api python -c "$py" 2>/dev/null || echo "status=unreachable"; } \
        | grep -E '^(status|version|db_revision|expected_revision)=' \
        | tr -cd 'A-Za-z0-9._=+\n-' | cut -c1-80
}

# Waits for a healthy API at the expected DB revision, and at $1 when given.
wait_healthy() {
    local version=${1:-} deadline=$((SECONDS + HEALTH_TIMEOUT)) s
    until s=$(health_summary) \
        && grep -qx 'status=healthy' <<<"$s" \
        && { [[ -z $version ]] || grep -qx "version=$version" <<<"$s"; } \
        && [[ $(sed -n 's/^db_revision=//p' <<<"$s") == "$(sed -n 's/^expected_revision=//p' <<<"$s")" ]]; do
        if (( SECONDS >= deadline )); then
            echo "Not healthy at $version after ${HEALTH_TIMEOUT}s: $(tr '\n' ' ' <<<"$s")" >&2
            return 1
        fi
        sleep "$HEALTH_POLL"
    done
    echo "$s"
}

# "<service>: <state> restarts=<n>" for the named services, or every container.
container_states() {
    local service id ids=()
    # Built at runtime: SSM treats a literal double brace in a document as a parameter.
    local o='{' c='}'
    local fmt="${o}${o}index .Config.Labels \"com.docker.compose.service\"${c}${c}: ${o}${o}.State.Status${c}${c} restarts=${o}${o}.RestartCount${c}${c}"
    if (($#)); then
        for service in "$@"; do
            id=$($COMPOSE ps -q "$service")
            if [[ -z $id ]]; then
                echo "$service: missing"
                continue
            fi
            ids+=("$id")
        done
    else
        for id in $($COMPOSE ps -a -q); do ids+=("$id"); done
    fi
    for id in ${ids[@]+"${ids[@]}"}; do
        $DOCKER inspect -f "$fmt" "$id"
    done
}

# Fails (with a fixed message) unless the dump is readable.
check_dump() {
    case "$1" in
        *.sql.gz) gzip -t "$1" ;;
        *) $COMPOSE exec -T postgres pg_restore --list < "$1" >/dev/null 2>&1 ;;
    esac || { echo "dump is not readable: $(basename "$1")" >&2; return 1; }
}

# rotate-keys.sh keeps every BACKUP_ENCRYPTION_KEY and SECRETS_ENCRYPTION_KEY
# value backups may need, in secrets/previous/<name>/ and on EC2 in SSM under
# $MEMORY_SSM_PREFIX/previous/<name>/. Each rotation adds <timestamp>-<pid>-0,
# the value it replaced, and -1, the new one. Nothing overwrites or deletes an
# entry: whether a backup or the live database still needs a value can't be
# told safely from here, and a kept value costs next to nothing.
KEYRING=secrets/previous
# The kept values of key $1 (its SSM name), one per line, for callers to
# capture: never print them.
previous_keys() {
    local f
    for f in "$KEYRING/$1"/*; do
        [[ -f $f ]] && { cat "$f"; echo; }
    done
    [[ -n ${MEMORY_SSM_PREFIX:-} ]] || return 0
    aws ssm get-parameters-by-path --path "$MEMORY_SSM_PREFIX/previous/$1" --with-decryption \
        --query 'Parameters[].Value' --output text 2>/dev/null | tr '\t' '\n' | awk '$0 != "None"' \
        || { echo "could not read the kept $1 values from SSM" >&2; return 1; }
}

# Re-encrypts the stored secrets under $2 (default: the configured key) from
# whichever of the keys in $3 and the kept ones each is under, logging to $1,
# and prints the count.
rekey_secrets() {
    local from out
    from=$(printf '%s\n' "${3:-}"; previous_keys secrets_encryption_key) || return 1
    out=$(ROTATE_SECRETS_TO=${2:-} ROTATE_SECRETS_FROM=$from $COMPOSE run --rm --no-deps -T \
        -e ROTATE_SECRETS_TO -e ROTATE_SECRETS_FROM --entrypoint python worker -m memory.common.db.rekey 2>>"$1") \
        || { echo "re-encrypting the stored secrets failed; details in $1 (owner only)" >&2; return 1; }
    grep -E '^secrets_reencrypted=[0-9]+$' <<<"$out" || return 1
}

# A dump from before a rotation holds the stored secrets under a kept key. With
# none kept (never rotated) there is nothing to move them from, and no release
# code runs.
rekey_restored() {
    local kept step=$RESTORE_STEP
    [[ $DRY_RUN == 1 ]] && return
    RESTORE_STEP=secrets
    kept=$(previous_keys secrets_encryption_key)
    [[ -z $kept ]] || rekey_secrets "$RESTORE_LOG"
    RESTORE_STEP=$step
}

# Replaces the database from a dump; the caller has stopped the app services,
# and calls rekey_restored once the image that will run on it is built.
# Stderr goes to an owner-only log because restore errors quote the offending row.
restore_postgres() {
    local dump=$1
    state_paths
    mkdir -p "$STATE_DIR"
    : > "$RESTORE_LOG"
    chmod 600 "$RESTORE_LOG"
    wait_for_postgres
    [[ $DRY_RUN == 1 ]] && return
    $COMPOSE exec -T postgres dropdb -U "$DB_USER" --force "$DB_NAME" 2>>"$RESTORE_LOG"
    $COMPOSE exec -T postgres createdb -U "$DB_USER" "$DB_NAME" 2>>"$RESTORE_LOG"
    case "$dump" in
        *.sql.gz)
            gunzip -c "$dump" | $COMPOSE exec -T postgres psql -q -v ON_ERROR_STOP=1 --single-transaction \
                -U "$DB_USER" -d "$DB_NAME" >/dev/null 2>>"$RESTORE_LOG" ;;
        *)
            $COMPOSE exec -T postgres pg_restore -U "$DB_USER" -d "$DB_NAME" --no-owner \
                < "$dump" >/dev/null 2>>"$RESTORE_LOG" ;;
    esac
}

# Armed before the first destructive step; prints a fixed line, never content.
# RESTORE_STEP names where it failed: qdrant-copy, qdrant-restore,
# qdrant-putback-failed, postgres, secrets, files, starting, or anything else.
RESTORE_STEP=preparing
arm_restore_failure_trap() {
    SAFETY_DUMP=${1:-}
    SAFETY_QDRANT=${2:-}
    trap restore_failed ERR
}
restore_failed() {
    trap - ERR
    state_paths
    mkdir -p "$STATE_DIR"
    date -u +%FT%TZ > "$RESTORE_FAILED_MARKER"
    local effect services="The app services are stopped." copies=""
    [[ -n ${SAFETY_DUMP:-} ]] && copies=" Safety copies: $SAFETY_DUMP${SAFETY_QDRANT:+ and $SAFETY_QDRANT}."
    case "$RESTORE_STEP" in
        qdrant-copy) effect="Qdrant could not be stopped or copied. Nothing was changed."; copies="" ;;
        qdrant-restore) effect="Qdrant was put back from its safety copy; Postgres and files are untouched." ;;
        qdrant-putback-failed) effect="Putting the previous Qdrant data back failed and Qdrant may be down; Postgres and files are untouched." ;;
        postgres) effect="Qdrant is already restored; the database may be dropped or partly loaded." ;;
        secrets) effect="The database is restored, but its stored secrets could not be moved to the current SECRETS_ENCRYPTION_KEY." ;;
        files) effect="Qdrant and Postgres are restored; some files may be missing." ;;
        starting)
            effect="Qdrant, Postgres and files are restored."
            services="The services were started but are not healthy."
            ;;
        *) effect="Data may be partly restored." ;;
    esac
    {
        echo "RESTORE FAILED in step $RESTORE_STEP: $effect $services"
        echo "Details are in $RESTORE_LOG (owner only).$copies"
    } >&2
}

# One request to the Qdrant API inside a container, prefix given as the command
# (e.g. "$COMPOSE exec -T qdrant"). The image has no curl; bash speaks HTTP.
# Prints the body and fails on a non-2xx status.
QDRANT_HTTP='exec 3<>/dev/tcp/localhost/6333; printf "%s %s HTTP/1.0\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n" "$0" "$1" >&3; cat <&3'
qdrant_request() {
    local method=$1 path=$2 response
    shift 2
    response=$("$@" bash -c "$QDRANT_HTTP" "$method" "$path" 2>>"$RESTORE_LOG") || return 1
    head -n1 <<<"$response" | grep -q '^HTTP/[0-9.]* 2' || return 1
    sed '1,/^.\{0,1\}$/d' <<<"$response"
}
wait_qdrant() {
    local _
    for _ in {1..60}; do
        qdrant_request GET /readyz "$@" >/dev/null && return
        sleep 2
    done
    echo "Qdrant did not become ready" >&2
    return 1
}

# Replaces Qdrant's data with a full-storage snapshot, as Qdrant documents it:
# a one-off container of the pinned image starts on the volume with
# --storage-snapshot. The volume is emptied first so exactly the snapshot's
# collections remain, after the old volume was tarred to $2 (the safety copy).
# Any failure puts that copy back and starts Qdrant again.
qdrant_exec() { $COMPOSE run --rm --no-deps -T --entrypoint "$@"; }
qdrant_apply_snapshot() {
    local snapshot=$1 oneoff=$2
    local live=($COMPOSE exec -T qdrant) inside=($DOCKER exec -i "$oneoff")
    qdrant_exec find qdrant /qdrant/storage -mindepth 1 -delete 2>>"$RESTORE_LOG" || return 1
    $COMPOSE run -d --no-deps --name "$oneoff" -v "$snapshot:/restore/full.snapshot:ro" qdrant \
        ./qdrant --storage-snapshot /restore/full.snapshot --force-snapshot >/dev/null 2>>"$RESTORE_LOG" || return 1
    wait_qdrant "${inside[@]}" || return 1
    qdrant_request GET /collections "${inside[@]}" >/dev/null || return 1
    $DOCKER stop "$oneoff" >/dev/null 2>>"$RESTORE_LOG" || return 1
    $DOCKER rm "$oneoff" >/dev/null 2>>"$RESTORE_LOG" || return 1
    $COMPOSE up -d qdrant 2>>"$RESTORE_LOG" || return 1
    wait_qdrant "${live[@]}" || return 1
    qdrant_request GET /collections "${live[@]}" >/dev/null
}
qdrant_put_back() {
    local safety=$1 oneoff=$2
    local live=($COMPOSE exec -T qdrant)
    $DOCKER rm -f "$oneoff" >/dev/null 2>&1 || true
    $COMPOSE stop qdrant 2>>"$RESTORE_LOG" || return 1
    qdrant_exec find qdrant /qdrant/storage -mindepth 1 -delete 2>>"$RESTORE_LOG" || return 1
    qdrant_exec tar qdrant -xzf - -C /qdrant/storage < "$safety" 2>>"$RESTORE_LOG" || return 1
    $COMPOSE up -d qdrant 2>>"$RESTORE_LOG" || return 1
    wait_qdrant "${live[@]}"
}
restore_qdrant() {
    local snapshot=$1 safety=$2 oneoff=memory-qdrant-restore-$$
    state_paths
    mkdir -p "$STATE_DIR"
    : >> "$RESTORE_LOG"
    if [[ $DRY_RUN == 1 ]]; then
        echo "+ stop qdrant, tar its volume to $safety, restore $snapshot"
        return
    fi
    RESTORE_STEP=qdrant-copy
    $COMPOSE stop qdrant 2>>"$RESTORE_LOG"
    qdrant_exec tar qdrant -czf - -C /qdrant/storage . > "$safety.partial" 2>>"$RESTORE_LOG" \
        && [[ -s $safety.partial ]] && tar -tzf "$safety.partial" >/dev/null 2>&1 \
        || { rm -f "$safety.partial"; $COMPOSE up -d qdrant; echo "could not copy the current Qdrant data; nothing was changed" >&2; return 1; }
    mv "$safety.partial" "$safety"
    RESTORE_STEP=qdrant-restore
    qdrant_apply_snapshot "$snapshot" "$oneoff" && return
    if qdrant_put_back "$safety" "$oneoff"; then
        echo "Qdrant step failed; the previous Qdrant data was put back from $safety" >&2
    else
        RESTORE_STEP=qdrant-putback-failed
        echo "Qdrant step failed AND putting the previous data back failed; the copy is $safety" >&2
    fi
    return 1
}
