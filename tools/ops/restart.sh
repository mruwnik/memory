#!/usr/bin/env bash
# Restart one compose service, or all of them, and report container states.
#
#   tools/ops/restart.sh [--dry-run] [SERVICE]
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
take_lock

service=${POSITIONAL[0]:-}
if [[ -n $service ]]; then
    services=$($COMPOSE config --services) || { echo "compose config failed" >&2; exit 1; }
    grep -qx -- "$service" <<<"$services" || { echo "unknown service: $service" >&2; exit 2; }
fi

run $COMPOSE restart ${service:+"$service"}
# restart leaves stopped services stopped; up -d starts those.
run $COMPOSE up -d ${service:+"$service"}
[[ $DRY_RUN == 1 ]] || container_states
