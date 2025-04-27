#!/usr/bin/env bash
set -euo pipefail

QUEUES=${QUEUES:-default}
CONCURRENCY=${CONCURRENCY:-2}
LOGLEVEL=${LOGLEVEL:-INFO}

HOSTNAME="${QUEUES%@*}@$(hostname)"

exec celery -A memory.workers.celery_app worker \
     -Q "${QUEUES}" \
     --concurrency="${CONCURRENCY}" \
     --hostname="${HOSTNAME}" \
     --loglevel="${LOGLEVEL}"