#!/usr/bin/env bash
# Update this deployment to a release. See UPDATING.md.
#
#   tools/update.sh [--dry-run] [--require-signed ALLOWED_SIGNERS] [--min-age-hours N] [REF]
#       REF defaults to the newest v* release tag. The two flags restrict the
#       choice, and an explicit REF, to tags signed by a key in ALLOWED_SIGNERS
#       and at least N hours old (tools/select_release.sh).
#
# Env: COMPOSE (default "docker compose"), DOCKER (default docker, or podman
#      when COMPOSE starts with podman), REMOTE (default origin),
#      BACKUP_DIR (default ./backups), KEEP_BACKUPS (default 5),
#      MIN_FREE_GB (default 5), HEALTH_TIMEOUT seconds (default 300).
set -Eeuo pipefail
umask 077

CALLER_DIR=$PWD
cd "$(dirname "$0")/.."

COMPOSE=${COMPOSE:-docker compose}
DOCKER=${DOCKER:-$([[ $COMPOSE == podman* ]] && echo podman || echo docker)}
REMOTE=${REMOTE:-origin}
BACKUP_DIR=${BACKUP_DIR:-backups}
KEEP_BACKUPS=${KEEP_BACKUPS:-5}
MIN_FREE_GB=${MIN_FREE_GB:-5}
HEALTH_TIMEOUT=${HEALTH_TIMEOUT:-300}
DB_USER=${DB_USER:-kb}
DB_NAME=${DB_NAME:-kb}
APP_SERVICES=(api worker agents-worker ingest-hub)
STATE=$BACKUP_DIR/update-in-progress
LAST_GOOD=$BACKUP_DIR/last-good

DRY_RUN=0
REF=""
TARGET=""
SELECT_ARGS=()
while (($#)); do
    case "$1" in
        --dry-run) DRY_RUN=1; shift ;;
        --require-signed)
            path=${2:?$1 needs a file}
            [[ $path == /* ]] || path=$CALLER_DIR/$path
            SELECT_ARGS+=("$1" "$path"); shift 2 ;;
        --min-age-hours)
            SELECT_ARGS+=("$1" "${2:?$1 needs a value}"); shift 2 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) REF=$1; shift ;;
    esac
done

for n in KEEP_BACKUPS MIN_FREE_GB HEALTH_TIMEOUT; do
    if ! [[ ${!n} =~ ^[1-9][0-9]*$ ]]; then
        echo "$n must be a positive integer, got '${!n}'" >&2
        exit 2
    fi
done

log() { echo "==> $*"; }
run() {
    echo "+ $*"
    [[ $DRY_RUN == 1 ]] || "$@"
}

if [[ $DRY_RUN == 0 ]]; then
    mkdir -p "$BACKUP_DIR"
    run_log="update-$(date +%Y%m%d-%H%M%S).log"
    ln -sfn "$run_log" "$BACKUP_DIR/update.log"
    # The logger ignores INT/TERM so Ctrl-C can't kill it before the rollback
    # instructions are written.
    exec > >(trap '' INT TERM; exec tee -a "$BACKUP_DIR/$run_log") 2>&1
fi
report_result() {
    local rc=$?
    echo "UPDATE RESULT: $( ((rc == 0)) && echo ok || echo failed ) (exit $rc)"
}
trap report_result EXIT
log "Update started $(date -u +%FT%TZ)"

if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    echo "Tracked files have local changes; commit or discard them first." >&2
    exit 1
fi

PREVIOUS=$(git describe --tags --always)
PREVIOUS_SHA=$(git rev-parse HEAD)
TARGET_SHA=""
BACKUP=""
MIGRATED=0
# A run that died part-way left its rollback target and backup here. Resume
# from it only while the checkout is still at that run's target; anything else
# means the file is left over from an older, abandoned update.
if [[ -f $STATE ]]; then
    # shellcheck source=/dev/null
    if ! source "$STATE" || [[ -z $TARGET_SHA || -z $PREVIOUS_SHA ]]; then
        echo "corrupt state file: $STATE" >&2
        exit 1
    fi
    if [[ $(git rev-parse HEAD) != "$TARGET_SHA" ]]; then
        echo "stale update state from $(date -r "$STATE" '+%F %T') at $STATE; delete it if the stack is healthy" >&2
        exit 1
    fi
    log "Resuming an unfinished update; rollback target stays $PREVIOUS"
fi
save_state() {
    [[ $DRY_RUN == 1 ]] && return
    printf 'PREVIOUS=%q\nPREVIOUS_SHA=%q\nTARGET_SHA=%q\nBACKUP=%q\nMIGRATED=%q\n' \
        "$PREVIOUS" "$PREVIOUS_SHA" "$TARGET_SHA" "$BACKUP" "$MIGRATED" > "$STATE.tmp"
    mv "$STATE.tmp" "$STATE"
}

on_failure() {
    trap - ERR
    local state
    state=$(printf %q "$STATE")
    echo
    echo "!! Update failed or was interrupted. The stack may be partially updated."
    echo "!! Rollback target: $PREVIOUS ($PREVIOUS_SHA). State: $STATE"
    if [[ -n $BACKUP && $MIGRATED == 1 ]]; then
        local b
        b=$(printf %q "$BACKUP")
        cat <<EOF
!! Migrations may have run. Postgres backup: $BACKUP
!! Re-running tools/update.sh resumes with the same rollback target. To roll back
!! instead, confirm with the owner first (this replaces the database), then run:
   $COMPOSE exec -T postgres pg_restore --list < $b > /dev/null && \\
   $COMPOSE stop ${APP_SERVICES[*]} && \\
   $COMPOSE exec -T postgres dropdb -U $DB_USER --force $DB_NAME && \\
   $COMPOSE exec -T postgres createdb -U $DB_USER $DB_NAME && \\
   $COMPOSE exec -T postgres pg_restore -U $DB_USER -d $DB_NAME --no-owner < $b && \\
   git checkout --detach $PREVIOUS_SHA && \\
   APP_VERSION=$PREVIOUS $COMPOSE up -d --build
EOF
    else
        echo "!! Migrations did not run; the database is untouched. To go back:"
        echo "   git checkout --detach $PREVIOUS_SHA && APP_VERSION=$PREVIOUS $COMPOSE up -d --build"
    fi
    echo "!! Once /health reports $PREVIOUS again, remove the update state: rm -f $state"
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

log "Fetching from $REMOTE"
git fetch --tags --force "$REMOTE"

if ((${#SELECT_ARGS[@]})); then
    ((DRY_RUN)) && SELECT_ARGS+=(--no-record)
    REF=$(tools/select_release.sh "${SELECT_ARGS[@]}" ${REF:+"$REF"})
    TARGET=$(git rev-parse --verify "refs/tags/$REF^{commit}")
elif [[ -z $REF ]]; then
    REF=$(git tag --list 'v*' --sort=-v:refname | head -n1)
    if [[ -z $REF ]]; then
        echo "No release tags (v*) found; pass a ref explicitly, e.g. $REMOTE/master" >&2
        exit 1
    fi
fi
TARGET=${TARGET:-$(git rev-parse --verify "$REF^{commit}")}
APP_VERSION=$(git describe --tags --always "$TARGET")
export APP_VERSION
log "Updating $PREVIOUS -> $REF ($APP_VERSION, $TARGET)"

wait_for_postgres
if [[ $DRY_RUN == 0 ]]; then
    db_bytes=$($COMPOSE exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -Atc "SELECT pg_database_size(current_database())")
    free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
    if ! [[ $db_bytes =~ ^[0-9]+$ && $free_kb =~ ^[0-9]+$ ]]; then
        echo "Could not determine database size ('$db_bytes') or free disk ('$free_kb')" >&2
        exit 1
    fi
    need_kb=$(( db_bytes / 1024 + MIN_FREE_GB * 1024 * 1024 ))
    if (( free_kb < need_kb )); then
        echo "Not enough disk: $((free_kb / 1024)) MiB free in $BACKUP_DIR, need $((need_kb / 1024)) MiB (database size + MIN_FREE_GB=$MIN_FREE_GB GiB)" >&2
        exit 1
    fi
fi

trap on_failure ERR
trap 'on_failure; exit 130' INT TERM

run git checkout --detach "$TARGET"
TARGET_SHA=$TARGET
save_state

log "Building images"
run $COMPOSE build --pull

# The new code's startup checks (required settings, matching queue prefixes)
# fail here while the old stack is still serving, not after it was stopped.
log "Checking settings against the new images"
run $COMPOSE run --rm --no-deps --entrypoint python worker -m memory.common.settings worker

log "Stopping app services"
run $COMPOSE stop "${APP_SERVICES[@]}"
wait_for_postgres

if [[ -n $BACKUP && -f $BACKUP ]]; then
    log "Keeping the pre-update backup from the earlier run: $BACKUP"
else
    dump="$BACKUP_DIR/postgres-$(date +%Y%m%d-%H%M%S)-$PREVIOUS.dump"
    log "Backing up Postgres to $dump"
    echo "+ $COMPOSE exec -T postgres pg_dump -U $DB_USER -Fc $DB_NAME > $dump"
    if [[ $DRY_RUN == 0 ]]; then
        $COMPOSE exec -T postgres pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$dump.partial"
        mv "$dump.partial" "$dump"
        BACKUP=$dump
        save_state
    fi
fi

log "Running migrations"
MIGRATED=1
save_state
run $COMPOSE run --rm --no-deps migrate

log "Starting stack"
run $COMPOSE up -d

if [[ $DRY_RUN == 1 ]]; then
    log "Dry run complete"
    exit 0
fi

log "Waiting for /health to report healthy at the expected DB revision"
CHECK='import json, urllib.request as u
d = json.load(u.urlopen("http://localhost:8000/health", timeout=5))
print(json.dumps(d))
assert d["status"] == "healthy" and d["db_revision"] == d["expected_revision"] and d["version"] == "'"$APP_VERSION"'"'
deadline=$((SECONDS + HEALTH_TIMEOUT))
until status=$($COMPOSE exec -T api python -c "$CHECK" 2>/dev/null); do
    if (( SECONDS >= deadline )); then
        echo "Stack not healthy after ${HEALTH_TIMEOUT}s. Last response: ${status:-none}" >&2
        false
    fi
    sleep 5
done
log "Healthy: $status"

container_states() {
    local service id
    for service in "${APP_SERVICES[@]}"; do
        id=$($COMPOSE ps -q "$service")
        if [[ -z $id ]]; then
            echo "$service: missing"
            continue
        fi
        echo "$service: $($DOCKER inspect -f '{{.State.Status}} restarts={{.RestartCount}}' "$id")"
    done
}
log "Checking that app containers stay up"
before=$(container_states)
sleep 15
after=$(container_states)
echo "$after"
if [[ $before != "$after" ]] || grep -qv ': running ' <<<"$after"; then
    echo "App containers are not stable. Before: $before" >&2
    false
fi

printf 'SHA=%q\nVERSION=%q\nBACKUP=%q\n' "$TARGET" "$APP_VERSION" "$BACKUP" > "$LAST_GOOD"
rm -f "$STATE"
trap - ERR INT TERM

shopt -s nullglob
dumps=("$BACKUP_DIR"/postgres-*.dump)
if (( ${#dumps[@]} > KEEP_BACKUPS )); then
    log "Pruning old backups, keeping the newest $KEEP_BACKUPS"
    rm -f -- "${dumps[@]:0:${#dumps[@]}-KEEP_BACKUPS}"
fi

log "Updated to $APP_VERSION. Backup kept at $BACKUP"
log "Run the post-deploy commands for this release from CHANGELOG.md."
