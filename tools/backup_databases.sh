#!/bin/bash
# Backup Postgres and Qdrant databases to S3

set -euo pipefail

# Install AWS CLI if not present (postgres:15 image doesn't include it)
if ! command -v aws >/dev/null 2>&1; then
    echo "Installing AWS CLI, wget, and jq..."
    apt-get update -qq && apt-get install -y -qq awscli wget jq >/dev/null 2>&1
fi

# Configuration - read from environment or use defaults
BUCKET="${S3_BACKUP_BUCKET:-equistamp-memory-backup}"
PREFIX="${S3_BACKUP_PREFIX:-Daniel}/databases"
REGION="${S3_BACKUP_REGION:-eu-central-1}"
PASSWORD="${BACKUP_ENCRYPTION_KEY:?BACKUP_ENCRYPTION_KEY not set}"
MAX_BACKUPS="${MAX_BACKUPS:-30}"  # Keep last N backups

# Service names (docker-compose network)
POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-kb}"
POSTGRES_DB="${POSTGRES_DB:-kb}"
QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"

# Timestamp for backups
DATE=$(date +%Y%m%d-%H%M%S)
DATE_SIMPLE=$(date +%Y%m%d)

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

error() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: $*" >&2
}

# Clean old backups - keep only last N
cleanup_old_backups() {
    local prefix=$1
    local pattern=$2  # e.g., "postgres-" or "qdrant-"
    
    log "Checking for old ${pattern} backups to clean up..."
    
    # List all backups matching pattern, sorted by date (oldest first)
    local backups
    backups=$(aws s3 ls "s3://${BUCKET}/${prefix}/" --region "${REGION}" | \
              grep "${pattern}" | \
              awk '{print $4}' | \
              sort)
    
    local count=$(echo "$backups" | wc -l)
    
    if [ "$count" -le "$MAX_BACKUPS" ]; then
        log "Found ${count} ${pattern} backups (max: ${MAX_BACKUPS}), no cleanup needed"
        return 0
    fi
    
    local to_delete=$((count - MAX_BACKUPS))
    log "Found ${count} ${pattern} backups, deleting ${to_delete} oldest..."
    
    echo "$backups" | head -n "$to_delete" | while read -r file; do
        if [ -n "$file" ]; then
            log "Deleting old backup: ${file}"
            aws s3 rm "s3://${BUCKET}/${prefix}/${file}" --region "${REGION}"
        fi
    done
}

# Backup Postgres
backup_postgres() {
    log "Starting Postgres backup..."
    
    local output_path="s3://${BUCKET}/${PREFIX}/postgres-${DATE_SIMPLE}.sql.gz.enc"
    
    # Use pg_dump directly with service name (no docker exec needed)
    export PGPASSWORD=$(cat "${POSTGRES_PASSWORD_FILE}")
    if pg_dump -h "${POSTGRES_HOST}" -U "${POSTGRES_USER}" "${POSTGRES_DB}" 2>/dev/null | \
       gzip | \
       openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:${PASSWORD}" | \
       aws s3 cp - "${output_path}" --region "${REGION}"; then
        log "Postgres backup completed: ${output_path}"
        unset PGPASSWORD
        cleanup_old_backups "${PREFIX}" "postgres-"
        return 0
    else
        error "Postgres backup failed"
        unset PGPASSWORD
        return 1
    fi
}

# Backup Qdrant
backup_qdrant() {
    log "Starting Qdrant backup..."
    
    # Create snapshot via HTTP API (no docker exec needed)
    local snapshot_response
    if ! snapshot_response=$(wget -q -O - --post-data='{}' \
                             --header='Content-Type: application/json' \
                             "${QDRANT_URL}/snapshots" 2>/dev/null); then
        error "Failed to create Qdrant snapshot"
        return 1
    fi
    
    local snapshot_name
    # Parse snapshot name - wget/busybox may not have jq, so use grep/sed
    if command -v jq >/dev/null 2>&1; then
        snapshot_name=$(echo "${snapshot_response}" | jq -r '.result.name // empty')
    else
        # Fallback: parse JSON without jq (fragile but works for simple case)
        snapshot_name=$(echo "${snapshot_response}" | grep -o '"name":"[^"]*"' | cut -d'"' -f4)
    fi
    
    if [ -z "${snapshot_name}" ]; then
        error "Could not extract snapshot name from response: ${snapshot_response}"
        return 1
    fi
    
    log "Created Qdrant snapshot: ${snapshot_name}"
    
    # Download snapshot and upload to S3
    local output_path="s3://${BUCKET}/${PREFIX}/qdrant-${DATE_SIMPLE}.snapshot.enc"
    
    if wget -q -O - "${QDRANT_URL}/snapshots/${snapshot_name}" | \
       openssl enc -aes-256-cbc -salt -pbkdf2 -pass "pass:${PASSWORD}" | \
       aws s3 cp - "${output_path}" --region "${REGION}"; then
        log "Qdrant backup completed: ${output_path}"
        
        # Delete the snapshot from Qdrant
        if wget -q -O - --method=DELETE \
           "${QDRANT_URL}/snapshots/${snapshot_name}" >/dev/null 2>&1; then
            log "Deleted Qdrant snapshot: ${snapshot_name}"
        else
            error "Failed to delete Qdrant snapshot: ${snapshot_name}"
        fi
        
        cleanup_old_backups "${PREFIX}" "qdrant-"
        return 0
    else
        error "Qdrant backup failed"
        
        # Try to clean up snapshot
        wget -q -O - --method=DELETE \
            "${QDRANT_URL}/snapshots/${snapshot_name}" >/dev/null 2>&1 || true
        
        return 1
    fi
}

# Main execution
main() {
    log "Database backup started"
    
    local postgres_result=0
    local qdrant_result=0
    
    # Backup Postgres
    if ! backup_postgres; then
        postgres_result=1
    fi
    
    # Backup Qdrant
    if ! backup_qdrant; then
        qdrant_result=1
    fi
    
    # Summary
    if [ $postgres_result -eq 0 ] && [ $qdrant_result -eq 0 ]; then
        log "All database backups completed successfully"
        return 0
    elif [ $postgres_result -ne 0 ] && [ $qdrant_result -ne 0 ]; then
        error "All database backups failed"
        return 1
    else
        error "Some database backups failed (Postgres: ${postgres_result}, Qdrant: ${qdrant_result})"
        return 1
    fi
}

# Run main function
main

