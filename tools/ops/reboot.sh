#!/usr/bin/env bash
# Reboot the machine in one minute.
#
#   tools/ops/reboot.sh --yes [--dry-run]
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"
require_yes
take_lock

run shutdown -r +1
