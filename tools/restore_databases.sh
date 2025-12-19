#!/bin/bash
# Restore Postgres and Qdrant databases from S3 backups
# Usage: ./restore_databases.sh [DATE]
# Example: ./restore_databases.sh 20251219

set -euo pipefail

# Configuration - read from environment or use defaults
BUCKET="${S3_BACKUP_BUCKET:-equistamp-memory-backup}"
PREFIX="${S3_BACKUP_PREFIX:-Daniel}/databases"
REGION="${S3_BACKUP_REGION:-eu-central-1}"
PASSWORD="${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY not set}"

# Target services - adjust for your environment
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-kb}"
POSTGRES_DB="${POSTGRES_DB:-kb}"
QDRANT_URL="${QDRANT_URL:-http://localhost:6333}"

# Date to restore (default: list available backups)
DATE="${1:-}"

# Temp directory for downloads
TEMP_DIR=$(mktemp -d)
trap "rm -rf ${TEMP_DIR}" EXIT

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# List available backups
list_backups() {
    log "Available PostgreSQL backups:"
    aws s3 ls "s3://${BUCKET}/${PREFIX}/" --region "${REGION}" | grep "postgres-" | awk '{print "  " $4}' | sort -r | head -10

    echo ""
    log "Available Qdrant backups:"
    aws s3 ls "s3://${BUCKET}/${PREFIX}/" --region "${REGION}" | grep "qdrant-" | awk '{print "  " $4}' | sort -r | head -10
}

# Restore PostgreSQL
restore_postgres() {
    local date=$1
    local s3_path="s3://${BUCKET}/${PREFIX}/postgres-${date}.sql.gz.enc"
    local sql_file="${TEMP_DIR}/postgres_restore.sql"

    log "Checking if Postgres backup exists: ${s3_path}"
    if ! aws s3 ls "${s3_path}" --region "${REGION}" >/dev/null 2>&1; then
        error "Postgres backup not found: ${s3_path}"
        return 1
    fi

    log "Downloading and decrypting Postgres backup..."
    if ! aws s3 cp "${s3_path}" - --region "${REGION}" | \
         openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:${PASSWORD}" | \
         gunzip > "${sql_file}"; then
        error "Failed to download/decrypt Postgres backup"
        return 1
    fi

    log "Postgres backup decrypted ($(du -h "${sql_file}" | cut -f1))"

    # Check if we can connect to postgres
    log "Testing PostgreSQL connection..."
    if ! PGPASSWORD="${PGPASSWORD:-}" psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "SELECT 1" >/dev/null 2>&1; then
        error "Cannot connect to PostgreSQL at ${POSTGRES_HOST}:${POSTGRES_PORT}"
        error "Set PGPASSWORD environment variable or check connection settings"
        log "SQL dump saved to: ${sql_file}"
        log "You can restore manually with: psql -h ${POSTGRES_HOST} -U ${POSTGRES_USER} -d ${POSTGRES_DB} < ${sql_file}"
        return 1
    fi

    log "Restoring to PostgreSQL..."
    if PGPASSWORD="${PGPASSWORD:-}" psql -h "${POSTGRES_HOST}" -p "${POSTGRES_PORT}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" < "${sql_file}"; then
        log "PostgreSQL restore completed successfully"
        return 0
    else
        error "PostgreSQL restore failed (some errors may be expected for existing objects)"
        return 1
    fi
}

# Restore Qdrant
restore_qdrant() {
    local date=$1
    local s3_path="s3://${BUCKET}/${PREFIX}/qdrant-${date}.snapshot.enc"
    local snapshot_file="${TEMP_DIR}/qdrant_restore.snapshot"

    log "Checking if Qdrant backup exists: ${s3_path}"
    if ! aws s3 ls "${s3_path}" --region "${REGION}" >/dev/null 2>&1; then
        error "Qdrant backup not found: ${s3_path}"
        return 1
    fi

    log "Downloading and decrypting Qdrant backup..."
    if ! aws s3 cp "${s3_path}" - --region "${REGION}" | \
         openssl enc -d -aes-256-cbc -pbkdf2 -pass "pass:${PASSWORD}" \
         > "${snapshot_file}"; then
        error "Failed to download/decrypt Qdrant backup"
        return 1
    fi

    log "Qdrant backup decrypted ($(du -h "${snapshot_file}" | cut -f1))"

    # Check if Qdrant is reachable
    log "Testing Qdrant connection..."
    if ! curl -sf "${QDRANT_URL}/readyz" >/dev/null 2>&1; then
        error "Cannot connect to Qdrant at ${QDRANT_URL}"
        log "Snapshot saved to: ${snapshot_file}"
        log "You can restore manually by uploading to Qdrant"
        return 1
    fi

    log "Uploading snapshot to Qdrant..."
    local upload_response
    if ! upload_response=$(curl -sf -X POST "${QDRANT_URL}/snapshots/upload?wait=true" \
         -H "Content-Type: multipart/form-data" \
         -F "snapshot=@${snapshot_file}" 2>&1); then
        error "Failed to upload snapshot to Qdrant: ${upload_response}"
        return 1
    fi

    log "Snapshot uploaded, recovering..."

    # Extract the snapshot filename from the response
    local snapshot_name
    if command -v jq >/dev/null 2>&1; then
        snapshot_name=$(echo "${upload_response}" | jq -r '.result.name // empty')
    else
        snapshot_name=$(echo "${upload_response}" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)
    fi

    if [ -z "${snapshot_name}" ]; then
        log "Upload response: ${upload_response}"
        log "Snapshot uploaded but could not extract name. Check Qdrant manually."
        return 0
    fi

    log "Recovering from snapshot: ${snapshot_name}"
    if curl -sf -X PUT "${QDRANT_URL}/snapshots/recover" \
         -H "Content-Type: application/json" \
         -d "{\"location\": \"file:///qdrant/snapshots/${snapshot_name}\"}" >/dev/null; then
        log "Qdrant restore completed successfully"
        return 0
    else
        error "Qdrant recovery failed"
        return 1
    fi
}

# Main
main() {
    if [ -z "${DATE}" ]; then
        log "No date specified. Listing available backups..."
        echo ""
        list_backups
        echo ""
        log "Usage: $0 <DATE>"
        log "Example: $0 20251219"
        exit 0
    fi

    log "Starting database restore for date: ${DATE}"
    echo ""

    local postgres_result=0
    local qdrant_result=0

    # Restore Postgres
    echo "=========================================="
    echo "  PostgreSQL Restore"
    echo "=========================================="
    if ! restore_postgres "${DATE}"; then
        postgres_result=1
    fi
    echo ""

    # Restore Qdrant
    echo "=========================================="
    echo "  Qdrant Restore"
    echo "=========================================="
    if ! restore_qdrant "${DATE}"; then
        qdrant_result=1
    fi
    echo ""

    # Summary
    echo "=========================================="
    echo "  Summary"
    echo "=========================================="
    if [ $postgres_result -eq 0 ] && [ $qdrant_result -eq 0 ]; then
        log "All database restores completed successfully"
        exit 0
    elif [ $postgres_result -ne 0 ] && [ $qdrant_result -ne 0 ]; then
        error "All database restores failed"
        exit 1
    else
        error "Some restores failed (Postgres: ${postgres_result}, Qdrant: ${qdrant_result})"
        exit 1
    fi
}

main
