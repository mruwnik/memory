#!/usr/bin/env bash
# Roll back to the version before the last update, restoring its pre-update
# database dump (UPDATING.md section 4). Everything written since is lost.
# Refuses unless the update record belongs to the current checkout, and
# consumes the record so a retry can't restore the same dump twice. The tag it
# leaves is added to $STATE_DIR/skip-tags so the nightly timer doesn't
# reinstall it.
#
#   tools/ops/rollback.sh --yes [--dry-run]
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
require_yes
take_lock

HEAD_SHA=$(git rev-parse HEAD)
PREVIOUS=""
PREVIOUS_SHA=""
TARGET_SHA=""
BACKUP=""
MIGRATED=1
if [[ -f $STATE ]]; then
    RECORD=$STATE
    # shellcheck source=/dev/null
    source "$STATE"
elif [[ -f $LAST_GOOD ]]; then
    RECORD=$LAST_GOOD
    SHA=""
    # shellcheck source=/dev/null
    source "$LAST_GOOD"
    TARGET_SHA=$SHA
else
    echo "no update state or last-good record in $BACKUP_DIR; nothing to roll back to" >&2
    exit 1
fi
if [[ -z $PREVIOUS_SHA || $TARGET_SHA != "$HEAD_SHA" ]]; then
    echo "$RECORD does not describe the current checkout; refusing to roll back" >&2
    exit 1
fi
if [[ $PREVIOUS_SHA == "$TARGET_SHA" ]]; then
    echo "the last update did not change the version; nothing to roll back to" >&2
    exit 1
fi

log "Rolling back to $PREVIOUS"
rekey_with_previous=0
if [[ $MIGRATED == 1 ]]; then
    [[ -f $BACKUP ]] || { echo "pre-update dump is missing: $BACKUP" >&2; exit 1; }
    [[ $DRY_RUN == 1 ]] || check_dump "$BACKUP"
    arm_restore_failure_trap
    RESTORE_STEP=postgres
    run $COMPOSE stop "${APP_SERVICES[@]}"
    restore_postgres "$BACKUP"
    # The pre-update dump may predate a key rotation. The release rolled back
    # to moves its secrets, so a broken current image can't block the rollback;
    # a release from before rotate-keys can't, and the current one does it now.
    if git cat-file -e "$PREVIOUS_SHA:src/memory/common/db/rekey.py" 2>/dev/null; then
        rekey_with_previous=1
    else
        rekey_restored
    fi
else
    arm_restore_failure_trap
    run $COMPOSE stop "${APP_SERVICES[@]}"
fi
left_tags=$(git tag --points-at "$HEAD_SHA" --list 'v*')
run git checkout --detach "$PREVIOUS_SHA"
export APP_VERSION=$PREVIOUS
run $COMPOSE build
((rekey_with_previous == 0)) || rekey_restored
run $COMPOSE up -d
[[ $DRY_RUN == 1 ]] && exit 0

wait_healthy "$PREVIOUS" > /dev/null
rm -f "$STATE" "$LAST_GOOD" "$UPDATE_FAILED_MARKER" "$RESTORE_FAILED_MARKER"
if [[ -n $left_tags ]]; then
    mkdir -p "$STATE_DIR"
    echo "$left_tags" >> "$SKIP_TAGS"
fi
log "Rolled back to $PREVIOUS"
health_summary
