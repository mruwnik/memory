#!/bin/bash
# Run a verification session against a snapshot or environment
# Usage: ./run-verification.sh [OPTIONS]
#
# Options:
#   --snapshot PATH       Path to snapshot tarball
#   --environment NAME    Name of environment (uses volume)
#   --repo URL            Git repository URL to clone
#   --github-token TOKEN  GitHub token for repo access
#   --image NAME          Docker image name (default: claude-cloud:latest)
#   --keep                Keep container after run (for debugging)
#   -h, --help            Show this help

set -euo pipefail

# Defaults
SNAPSHOT=""
ENVIRONMENT=""
REPO_URL=""
GITHUB_TOKEN=""
IMAGE="claude-cloud:latest"
KEEP_CONTAINER=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --snapshot)
            SNAPSHOT="$2"
            shift 2
            ;;
        --environment)
            ENVIRONMENT="$2"
            shift 2
            ;;
        --repo)
            REPO_URL="$2"
            shift 2
            ;;
        --github-token)
            GITHUB_TOKEN="$2"
            shift 2
            ;;
        --image)
            IMAGE="$2"
            shift 2
            ;;
        --keep)
            KEEP_CONTAINER=true
            shift
            ;;
        -h|--help)
            head -20 "$0" | tail -n +2 | sed 's/^# //' | sed 's/^#//'
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Create temp directory for logs
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

# Build docker run command
DOCKER_ARGS=(
    --rm
    -e "CLAUDE_EXECUTABLE=/verify-setup.sh"
    -v "$TMPDIR:/var/log/claude"
)

# Add snapshot mount if specified
if [[ -n "$SNAPSHOT" ]]; then
    if [[ ! -f "$SNAPSHOT" ]]; then
        echo "Error: Snapshot file not found: $SNAPSHOT"
        exit 1
    fi
    SNAPSHOT_DIR=$(mktemp -d)
    trap "rm -rf $TMPDIR $SNAPSHOT_DIR" EXIT
    cp "$SNAPSHOT" "$SNAPSHOT_DIR/snapshot.tar.gz"
    DOCKER_ARGS+=(-v "$SNAPSHOT_DIR:/snapshot:ro")
    echo "Using snapshot: $SNAPSHOT"
fi

# Add environment volume if specified
if [[ -n "$ENVIRONMENT" ]]; then
    DOCKER_ARGS+=(-v "claude-env-$ENVIRONMENT:/home/claude")
    echo "Using environment: $ENVIRONMENT"
fi

# Add repo URL if specified
if [[ -n "$REPO_URL" ]]; then
    DOCKER_ARGS+=(-e "GIT_REPO_URL=$REPO_URL")
    echo "Repository: $REPO_URL"
fi

# Add GitHub token if specified
if [[ -n "$GITHUB_TOKEN" ]]; then
    DOCKER_ARGS+=(-e "GITHUB_TOKEN=$GITHUB_TOKEN")
    echo "GitHub token: provided"
fi

# Keep container if requested
if [[ "$KEEP_CONTAINER" == "true" ]]; then
    # Remove --rm and add name
    DOCKER_ARGS=("${DOCKER_ARGS[@]/--rm/}")
    DOCKER_ARGS+=(--name "claude-verify-$(date +%s)")
fi

echo ""
echo "=== Running Verification ==="
echo ""

# Run the container
# The entrypoint will run verify-setup.sh instead of claude
docker run "${DOCKER_ARGS[@]}" "$IMAGE"
EXIT_CODE=$?

echo ""
echo "=== Session Logs ==="
echo ""

if [[ -f "$TMPDIR/session.log" ]]; then
    cat "$TMPDIR/session.log"
else
    echo "(no session log)"
fi

echo ""
echo "=== Differ Logs ==="
echo ""

if [[ -f "$TMPDIR/differ.log" ]]; then
    cat "$TMPDIR/differ.log"
else
    echo "(no differ log)"
fi

echo ""
echo "=== Result ==="
echo ""

if [[ $EXIT_CODE -eq 0 ]]; then
    echo "Verification PASSED"
else
    echo "Verification FAILED (exit code: $EXIT_CODE)"
fi

exit $EXIT_CODE
