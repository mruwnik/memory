#!/usr/bin/env bash
# Nightly app update (memory-update.timer). First re-renders the host config
# from SSM (configure.sh), so a re-apply that removes a signer or changes the
# delay holds from this run, for update-to too. Then, unless auto_update is
# off, installs the newest signed release at least that old with
# tools/update.sh and runs the new release's configure.sh, so its units and
# timers replace the old ones. A tag that fails to install is added to
# skip-tags: it isn't retried, but a newer release still is. Writes "ok" or
# "failed" as the first word of $STATE_DIR/last-update-check.
#
# Env: MEMORY_SSM_PREFIX and AWS_DEFAULT_REGION (ops.env), ALLOWED_SIGNERS
#      (default /etc/memory/allowed_signers), REMOTE (default origin).
set -Eeuo pipefail
# shellcheck source-path=SCRIPTDIR source=../../../tools/ops/lib.sh
source "$(dirname "$0")/../../../tools/ops/lib.sh"

ALLOWED_SIGNERS=${ALLOWED_SIGNERS:-/etc/memory/allowed_signers}
export ALLOWED_SIGNERS OPS_ENV

mkdir -p "$STATE_DIR"
# shellcheck disable=SC2329  # called by the EXIT trap
finish() {
    local rc=$?
    echo "$( ((rc)) && echo failed || echo ok) $(date -u +%FT%TZ)" > "$STATE_DIR/last-update-check"
}
trap finish EXIT

auto_update=$(aws ssm get-parameter --name "${MEMORY_SSM_PREFIX:?}/auto_update" --query Parameter.Value --output text)
if [[ ! $auto_update =~ ^(true|false)$ ]]; then
    echo "unusable auto_update in SSM; keeping the current config" >&2
    exit 1
fi
deploy/aws/host/configure.sh
delay=$(sed -n 's/^MIN_AGE_HOURS=//p' "$OPS_ENV")

if [[ $auto_update != true ]]; then
    log "auto_update is off"
    exit 0
fi

git fetch --quiet --tags --force "${REMOTE:-origin}"
rc=0
tag=$(tools/select_release.sh --require-signed "$ALLOWED_SIGNERS" --min-age-hours "$delay") || rc=$?
# 3 is "up to date": checked here so a quiet night leaves no update log.
((rc == 3)) && exit 0
((rc == 0)) || exit "$rc"
if ! tools/update.sh --require-signed "$ALLOWED_SIGNERS" --min-age-hours "$delay" "$tag"; then
    echo "$tag" >> "$SKIP_TAGS"
    exit 1
fi
deploy/aws/host/configure.sh
