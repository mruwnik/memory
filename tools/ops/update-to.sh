#!/usr/bin/env bash
# Update to a release tag, enforcing the same signature and age checks as the
# nightly timer. Prints the step lines and the result line of the update.
#
#   tools/ops/update-to.sh [--dry-run] TAG
#
# Env: ALLOWED_SIGNERS (default /etc/memory/allowed_signers),
#      MIN_AGE_HOURS (default 48), UPDATE_SH (default tools/update.sh).
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
take_lock

tag=${POSITIONAL[0]:-}
if ! [[ $tag =~ ^v[0-9][A-Za-z0-9._-]*$ ]]; then
    echo "usage: update-to.sh [--dry-run] vX.Y.Z" >&2
    exit 2
fi

flags=()
[[ $DRY_RUN == 1 ]] && flags+=(--dry-run)
rc=0
out=$("${UPDATE_SH:-$ROOT/tools/update.sh}" \
    --require-signed "${ALLOWED_SIGNERS:-/etc/memory/allowed_signers}" \
    --min-age-hours "${MIN_AGE_HOURS:-48}" \
    ${flags[@]+"${flags[@]}"} \
    "$tag" 2>&1) || rc=$?
# Step and result lines only: migrate, git and compose output can carry rows
# or remote URLs. The full log stays on the box in $BACKUP_DIR/update.log.
grep -E '^(==> |UPDATE RESULT)' <<<"$out" || true
exit "$rc"
