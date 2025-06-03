#!/usr/bin/env bash
set -euo pipefail

QUEUE_PREFIX=${QUEUE_PREFIX:-memory}
QUEUES=${QUEUES:-default}
QUEUES=$(IFS=,; echo "${QUEUES}" | tr ',' '\n' | sed "s/^/${QUEUE_PREFIX}-/" | paste -sd, -)
CONCURRENCY=${CONCURRENCY:-2}
LOGLEVEL=${LOGLEVEL:-INFO}

HOSTNAME="${QUEUES%@*}@$(hostname)"

exec celery -A memory.common.celery_app worker \
     -Q "${QUEUES}" \
     --concurrency="${CONCURRENCY}" \
     --hostname="${HOSTNAME}" \
     --loglevel="${LOGLEVEL}"