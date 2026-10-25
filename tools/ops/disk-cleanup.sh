#!/usr/bin/env bash
# Free disk: prune unused images and build cache, database dumps beyond
# KEEP_BACKUPS (update, pre-restore and pre-rotate dumps and Qdrant tarballs are
# counted separately), and what killed runs left. Prints the approximate bytes freed.
#
#   tools/ops/disk-cleanup.sh [--dry-run]
#
# Env: PRUNE_AGE (default 168h): only images and cache older than this go.
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
take_lock

shopt -s nullglob
before=$(free_bytes)
for pattern in 'postgres-*.dump' 'pre-restore-[0-9]*.dump' 'pre-restore-qdrant-*.tar.gz' 'pre-rotate-*.dump'; do
    dumps=("$BACKUP_DIR"/$pattern)
    excess=$(( ${#dumps[@]} - KEEP_BACKUPS ))
    (( excess > 0 )) || continue
    for dump in "${dumps[@]:0:excess}"; do
        run rm -f -- "$dump"
    done
done
# A partial is from a killed run. The age check covers hosts without flock,
# where a dump may still be being written. A rotation's work copies matter
# only while its marker says it is unsettled.
while IFS= read -r -d '' leftover; do
    run rm -f -- "$leftover"
done < <(find "$BACKUP_DIR" -maxdepth 1 -name '*.partial' -mmin +1440 -print0 2>/dev/null)
if [[ ! -e $ROTATE_MARKER ]]; then
    for leftover in "$STATE_DIR"/rotate-work.*; do
        run rm -rf -- "$leftover"
    done
fi
run $DOCKER image prune -af --filter "until=${PRUNE_AGE:-168h}"
run $DOCKER builder prune -af --filter "until=${PRUNE_AGE:-168h}"
if [[ $DRY_RUN == 0 ]]; then
    freed=$(( $(free_bytes) - before ))
    echo "freed_bytes=$(( freed > 0 ? freed : 0 ))"
fi
