#!/usr/bin/env bash
set -euo pipefail

# SSH Setup for git operations
if [ -f /run/secrets/ssh_private_key ]; then
    echo "Setting up SSH keys for git operations..."
    mkdir -p ~/.ssh
    cp /run/secrets/ssh_private_key ~/.ssh/id_rsa
    cp /run/secrets/ssh_public_key ~/.ssh/id_rsa.pub
    cp /run/secrets/ssh_known_hosts ~/.ssh/known_hosts
    chmod 700 ~/.ssh
    chmod 600 ~/.ssh/id_rsa
    chmod 644 ~/.ssh/id_rsa.pub ~/.ssh/known_hosts
    echo "SSH keys configured successfully"
fi

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