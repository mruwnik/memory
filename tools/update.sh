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
#      MIN_FREE_GB (default 5), HEALTH_TIMEOUT seconds (default 300), STATE_DIR
#      (default /var/lib/memory). Shared defaults live in tools/ops/lib.sh.
#      A failed update leaves $STATE_DIR/update-failed; a success removes it.
set -Eeuo pipefail
umask 077

CALLER_DIR=$PWD
# shellcheck source=ops/lib.sh
source "$(dirname "$0")/ops/lib.sh"

REMOTE=${REMOTE:-origin}
MIN_FREE_GB=${MIN_FREE_GB:-5}

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

if ! [[ $MIN_FREE_GB =~ ^[1-9][0-9]*$ ]]; then
    echo "MIN_FREE_GB must be a positive integer, got '$MIN_FREE_GB'" >&2
    exit 2
fi

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
    [[ $DRY_RUN == 1 ]] || { mkdir -p "$STATE_DIR"; date -u +%FT%TZ > "$UPDATE_FAILED_MARKER"; }
    echo
    echo "!! Update failed or was interrupted. The stack may be partially updated."
    if [[ -z $TARGET_SHA ]]; then
        echo "!! The checkout was not changed; there is nothing to roll back."
        return
    fi
    echo "!! Rollback target: $PREVIOUS ($PREVIOUS_SHA). State: $STATE"
    if [[ -n $BACKUP && $MIGRATED == 1 ]]; then
        echo "!! Migrations may have run. Postgres backup: $BACKUP"
        echo "!! Re-running tools/update.sh resumes with the same rollback target. To roll back"
        echo "!! instead, confirm with the owner first (this replaces the database), then run:"
    else
        echo "!! Migrations did not run; the database is untouched. To go back, run:"
    fi
    echo "   tools/ops/rollback.sh --yes"
}

log "Fetching from $REMOTE"
git fetch --tags --force "$REMOTE"

if ((${#SELECT_ARGS[@]})); then
    ((DRY_RUN)) && SELECT_ARGS+=(--no-record)
    select_err=$(mktemp)
    select_rc=0
    REF=$(tools/select_release.sh "${SELECT_ARGS[@]}" ${REF:+"$REF"} 2> "$select_err") || select_rc=$?
    if ((select_rc == 3)); then
        log "Already up to date: $(cat "$select_err")"
        rm -f "$select_err"
        if [[ -f $STATE ]]; then
            echo "but an earlier update did not finish: $STATE" >&2
            exit 1
        fi
        exit 0
    elif ((select_rc)); then
        log "Release check failed: $(cat "$select_err")"
        rm -f "$select_err"
        exit 1
    fi
    rm -f "$select_err"
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
if [[ ! -f $STATE && $TARGET == "$PREVIOUS_SHA" ]]; then
    log "Already at $REF ($APP_VERSION); nothing to do"
    exit 0
fi
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
status=$(wait_healthy "$APP_VERSION" | tr '\n' ' ')
log "Healthy: $status"

log "Checking that app containers stay up"
before=$(container_states "${APP_SERVICES[@]}")
sleep 15
after=$(container_states "${APP_SERVICES[@]}")
echo "$after"
if [[ $before != "$after" ]] || grep -qv ': running ' <<<"$after"; then
    echo "App containers are not stable. Before: $before" >&2
    false
fi

printf 'SHA=%q\nVERSION=%q\nBACKUP=%q\nPREVIOUS=%q\nPREVIOUS_SHA=%q\n' \
    "$TARGET" "$APP_VERSION" "$BACKUP" "$PREVIOUS" "$PREVIOUS_SHA" > "$LAST_GOOD"
rm -f "$STATE" "$UPDATE_FAILED_MARKER"
unskip_tag "$REF"
trap - ERR INT TERM

shopt -s nullglob
dumps=("$BACKUP_DIR"/postgres-*.dump)
if (( ${#dumps[@]} > KEEP_BACKUPS )); then
    log "Pruning old backups, keeping the newest $KEEP_BACKUPS"
    rm -f -- "${dumps[@]:0:${#dumps[@]}-KEEP_BACKUPS}"
fi

log "Updated to $APP_VERSION. Backup kept at $BACKUP"
log "Run the post-deploy commands for this release from CHANGELOG.md."
