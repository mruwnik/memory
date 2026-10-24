#!/usr/bin/env bash
# Free disk: prune unused images and build cache, and database dumps beyond
# KEEP_BACKUPS (update dumps, pre-restore dumps and Qdrant tarballs are counted separately). Prints
# the approximate bytes freed.
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
for pattern in 'postgres-*.dump' 'pre-restore-[0-9]*.dump' 'pre-restore-qdrant-*.tar.gz'; do
    dumps=("$BACKUP_DIR"/$pattern)
    excess=$(( ${#dumps[@]} - KEEP_BACKUPS ))
    (( excess > 0 )) || continue
    for dump in "${dumps[@]:0:excess}"; do
        run rm -f -- "$dump"
    done
done
run $DOCKER image prune -af --filter "until=${PRUNE_AGE:-168h}"
run $DOCKER builder prune -af --filter "until=${PRUNE_AGE:-168h}"
if [[ $DRY_RUN == 0 ]]; then
    freed=$(( $(free_bytes) - before ))
    echo "freed_bytes=$(( freed > 0 ? freed : 0 ))"
fi
