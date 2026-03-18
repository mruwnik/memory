#!/usr/bin/env bash
set -euo pipefail

# SSH Setup for git operations
if [ -r /run/secrets/ssh_private_key ]; then
    echo "Setting up SSH keys for git operations..."
    perms=$(stat -c %a /run/secrets/ssh_private_key 2>/dev/null || stat -f %Lp /run/secrets/ssh_private_key)
    if [ "$perms" = "600" ] || [ "$perms" = "400" ]; then
        # Podman bind-mount — use key directly
        SSH_KEY=/run/secrets/ssh_private_key
        if [ -r /run/secrets/ssh_known_hosts ]; then
            SSH_HOSTS=/run/secrets/ssh_known_hosts
        fi
    else
        # Docker 0444 secrets — copy to /tmp with correct perms
        SSH_KEY=$(mktemp /tmp/ssh_key.XXXXXX)
        cp /run/secrets/ssh_private_key "$SSH_KEY"
        chmod 600 "$SSH_KEY"
        if [ -r /run/secrets/ssh_known_hosts ]; then
            SSH_HOSTS=$(mktemp /tmp/ssh_hosts.XXXXXX)
            cp /run/secrets/ssh_known_hosts "$SSH_HOSTS"
        fi
        trap 'rm -f "$SSH_KEY" "${SSH_HOSTS:-}" 2>/dev/null' EXIT
    fi
    if [ -n "${SSH_HOSTS:-}" ]; then
        export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o UserKnownHostsFile=$SSH_HOSTS -o StrictHostKeyChecking=yes"
    else
        echo "Warning: ssh_known_hosts not found, using StrictHostKeyChecking=accept-new"
        export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new"
    fi
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