#!/usr/bin/env bash
# Rotate the keys Memory generates itself: SECRETS_ENCRYPTION_KEY (re-encrypting
# the stored secrets), POSTGRES_PASSWORD, REDIS_PASSWORD, BACKUP_ENCRYPTION_KEY,
# and TRANSFER_TOKEN_SECRET when .env sets it (otherwise it is derived from
# SECRETS_ENCRYPTION_KEY and changes with it). A new SECRETS_ENCRYPTION_KEY
# ends transfer links, password reset links and OAuth sign-ins in flight.
# Provider API keys are not Memory's to rotate.
#
#   tools/ops/rotate-keys.sh --yes [--dry-run] [--only=NAME[,NAME...]]
#
# Refuses without the database's size plus MIN_FREE_GB (default 5) free in
# $BACKUP_DIR. Keeps a fresh dump as $BACKUP_DIR/pre-rotate-<timestamp>.dump
# (the newest KEEP_BACKUPS of them), stops the app services, keeps the
# BACKUP_ENCRYPTION_KEY and SECRETS_ENCRYPTION_KEY values backups need (KEYRING
# in lib.sh, kept indefinitely), writes the new values
# where the stack reads them (secrets/, .env, ops.env) and, with
# MEMORY_SSM_PREFIX set (EC2), to SSM, changes the Postgres password,
# re-encrypts the stored secrets and recreates the containers. A failure puts
# the old values back and restarts, except where the database may already be
# on the new one: that key stays new, and $ROTATE_MARKER names it. New values
# are made here and never printed: the output is key names, counts and states.
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"

KEYS="SECRETS_ENCRYPTION_KEY POSTGRES_PASSWORD REDIS_PASSWORD BACKUP_ENCRYPTION_KEY TRANSFER_TOKEN_SECRET"
# Backups need every value of these that was in use while they were taken.
KEPT="SECRETS_ENCRYPTION_KEY BACKUP_ENCRYPTION_KEY"
# The SecureStrings deploy/aws creates under $MEMORY_SSM_PREFIX.
IN_SSM="SECRETS_ENCRYPTION_KEY POSTGRES_PASSWORD REDIS_PASSWORD BACKUP_ENCRYPTION_KEY"

only=""
args=()
while (($#)); do
    case "$1" in
        --only) only=${2:-}; shift; (($#)) && shift ;;
        --only=*) only=${1#--only=}; shift ;;
        *) args+=("$1"); shift ;;
    esac
done
parse_args ${args[@]+"${args[@]}"}

IFS=', ' read -r -a selected <<<"${only:-$KEYS}"
in_list() { [[ " $2 " == *" $1 "* ]]; }
for name in ${selected[@]+"${selected[@]}"}; do
    in_list "$name" "$KEYS" || { echo "unknown key: $name (rotatable: $KEYS)" >&2; exit 2; }
done
require_yes
take_lock

lower() { tr '[:upper:]' '[:lower:]' <<<"$1"; }
# The last NAME=value line of env file $1 for $2, quotes removed.
env_value() {
    [[ -f $1 ]] || return 0
    sed -n "s/^$2=//p" "$1" | tail -n1 | sed -E "s/^['\"]//; s/['\"]\$//"
}
# Where key $1 is kept: "file PATH" or "env PATH" (a NAME=value line); nothing when unset.
store_of() {
    local file
    case "$1" in
        SECRETS_ENCRYPTION_KEY|POSTGRES_PASSWORD)
            file=secrets/$(lower "$1").txt
            [[ ! -s $file ]] || echo "file $file" ;;
        REDIS_PASSWORD|TRANSFER_TOKEN_SECRET)
            [[ -z $(env_value .env "$1") ]] || echo "env .env" ;;
        BACKUP_ENCRYPTION_KEY)
            file=$(env_value .env BACKUP_ENCRYPTION_KEY_FILE)
            if [[ -n $(env_value .env "$1") ]]; then echo "env .env"
            elif [[ -n $file && -s $file ]]; then echo "file $file"
            elif [[ -n $(env_value "$OPS_ENV" "$1") ]]; then echo "env $OPS_ENV"
            fi ;;
    esac
}
read_value() {
    case "$2" in
        file) cat "$3" ;;
        env) env_value "$3" "$1" ;;
    esac
}
# The value travels through the environment, never argv.
write_value() {
    case "$2" in
        file) printf %s "$4" > "$3" ;;
        env)
            VALUE=$4 awk -v key="$1" 'index($0, key "=") == 1 { print key "=" ENVIRON["VALUE"]; next } { print }' \
                "$3" > "$work/env"
            cat "$work/env" > "$3"
            rm -f "$work/env" ;;
    esac
    chmod 600 "$3"
}

rotate=()
for name in ${selected[@]+"${selected[@]}"}; do
    store=$(store_of "$name")
    if [[ -z $store ]]; then
        if [[ $name == TRANSFER_TOKEN_SECRET ]]; then
            echo "TRANSFER_TOKEN_SECRET: derived from SECRETS_ENCRYPTION_KEY, changes with it"
        else
            echo "$name: not configured, skipped"
        fi
        continue
    fi
    rotate+=("$name")
    printf -v "STORE_$name" %s "$store"
done
((${#rotate[@]})) || { echo "nothing to rotate"; exit 0; }
SSM=${MEMORY_SSM_PREFIX:-}
# deploy/aws names the key alias like the parameter prefix.
KMS_KEY=alias/${SSM#/}
log "Rotating ${rotate[*]}"
if [[ $DRY_RUN == 1 ]]; then
    for name in "${rotate[@]}"; do
        store=STORE_$name
        read -r kind path <<<"${!store}"
        echo "$name: $path${SSM:+ and SSM}"
    done
    exit 0
fi

mkdir -p "$STATE_DIR"
LOG=$STATE_DIR/rotate-keys.log
: >> "$LOG"
chmod 600 "$LOG"
work=$(mktemp -d "$STATE_DIR/rotate-work.XXXXXX")
# shellcheck disable=SC2034  # read by the EXIT trap
KEEP_WORK=0
# The pid keeps two rotations in one second from sharing a name.
STAMP=$(date -u +%Y%m%dT%H%M%SZ)-$$
dump=$BACKUP_DIR/pre-rotate-$STAMP.dump
trap 'rm -f "$dump.partial"; ((KEEP_WORK)) || rm -rf "$work"' EXIT
for name in "${rotate[@]}"; do
    store=STORE_$name
    read -r kind path <<<"${!store}"
    printf -v "OLD_$name" %s "$(read_value "$name" "$kind" "$path")"
    printf -v "NEW_$name" %s "$(openssl rand -hex 32)"
done
old() { local v=OLD_$1; printf %s "${!v}"; }
new() { local v=NEW_$1; printf %s "${!v}"; }

ssm_put() {
    local json=$work/parameter.json rc=0
    printf '{"Name": "%s", "Value": "%s", "Type": "SecureString", "KeyId": "%s", "Overwrite": true}' \
        "$1" "$2" "$KMS_KEY" > "$json"
    aws ssm put-parameter --cli-input-json "file://$json" > /dev/null 2>&1 || rc=$?
    rm -f "$json"
    ((rc == 0)) || { echo "could not write $1 to SSM" >&2; return 1; }
}
# The statement carries the password: keep it out of the server log, even on error.
set_db_password() {
    [[ $1 != *\'* ]] || { echo "the Postgres password contains a quote" >&2; return 1; }
    printf "SET log_statement = 'none';\nSET log_min_error_statement = 'panic';\nSET log_min_duration_statement = -1;\nALTER ROLE \"%s\" PASSWORD '%s';\n" \
        "$DB_USER" "$1" \
        | $COMPOSE exec -T postgres psql -q -v ON_ERROR_STOP=1 -U "$DB_USER" -d "$DB_NAME" > /dev/null 2>&1 \
        || { echo "could not change the Postgres password" >&2; return 1; }
}

# Keeps value $3 of key $1 as keyring entry $2, on the box and in SSM.
keep_key() {
    local dir
    dir=$KEYRING/$(lower "$1")
    mkdir -p "$dir"
    printf %s "$3" > "$dir/$2"
    [[ -z $SSM ]] || ssm_put "$SSM/previous/$(lower "$1")/$2" "$3"
}

# Each file a key lives in, copied before the first write.
stores=()
save_stores() {
    local name store kind path
    for name in "${rotate[@]}"; do
        store=STORE_$name
        read -r kind path <<<"${!store}"
        in_list "$path" "${stores[*]:-}" && continue
        cp -p "$path" "$work/store-${#stores[@]}"
        stores+=("$path")
    done
}
# Puts each back but the files in $1.
restore_stores() {
    local i
    for i in ${stores[@]+"${!stores[@]}"}; do
        in_list "${stores[i]}" "$1" && continue
        cat "$work/store-$i" > "${stores[i]}" || return 1
    done
}

STEP=preparing
STOPPED=0
SSM_WRITTEN=()
# Which value the database is on: old, new, or unknown while a change runs or
# after it failed (it may have committed).
DB_KEY=old
PG_ROLE=old
rotate_failed() {
    trap - ERR
    # A failure inside a command substitution reaches its caller, which lands here.
    ((BASH_SUBSHELL == 0)) || exit 1
    set +e
    local undone=1 healthy=1 name store kind path kept_new=() unsettled=() settle=() keep_files=()
    echo "ROTATION FAILED in step $STEP; putting the previous keys back" >&2
    # Nothing may write secrets under the new key while they move back.
    [[ $STEP != starting ]] || $COMPOSE stop "${APP_SERVICES[@]}" 2>>"$LOG" || undone=0
    # A value the database is on stays in place, or the stack couldn't read it.
    # After a failed change it is most likely still on the old one, and both
    # secrets keys are in the keyring either way.
    if [[ $DB_KEY != old ]] && ! rekey_secrets "$LOG" "$(old SECRETS_ENCRYPTION_KEY)" "$(new SECRETS_ENCRYPTION_KEY)" > /dev/null; then
        [[ $DB_KEY == new ]] && kept_new+=(SECRETS_ENCRYPTION_KEY) || { unsettled+=(SECRETS_ENCRYPTION_KEY); undone=0; }
    fi
    if [[ $PG_ROLE != old ]] && ! set_db_password "$(old POSTGRES_PASSWORD)"; then
        [[ $PG_ROLE == new ]] && kept_new+=(POSTGRES_PASSWORD) || { unsettled+=(POSTGRES_PASSWORD); undone=0; }
    fi
    for name in ${kept_new[@]+"${kept_new[@]}"}; do
        store=STORE_$name
        read -r kind path <<<"${!store}"
        keep_files+=("$path")
    done
    restore_stores "${keep_files[*]:-}" || undone=0
    for name in ${SSM_WRITTEN[@]+"${SSM_WRITTEN[@]}"}; do
        in_list "$name" "${kept_new[*]:-}" || ssm_put "$SSM/$(lower "$name")" "$(old "$name")" || undone=0
    done
    if ((STOPPED)); then
        { $COMPOSE up -d --force-recreate 2>>"$LOG" && wait_healthy > /dev/null; } || healthy=0
    fi
    echo "failed kept_new=$(IFS=,; echo "${kept_new[*]:-}") unsettled=$(IFS=,; echo "${unsettled[*]:-}")" > "$ROTATE_MARKER"
    if ((${#kept_new[@]})); then
        echo "The database is on the new ${kept_new[*]}, so those stay in place." >&2
    fi
    if ((${#unsettled[@]})); then
        echo "The database may be on the new ${unsettled[*]}, but the files hold the previous ones." >&2
    fi
    settle=(${kept_new[@]+"${kept_new[@]}"} ${unsettled[@]+"${unsettled[@]}"})
    if ((${#settle[@]})); then
        echo "Once the cause in $LOG is fixed, run rotate-keys.sh --yes --only=$(IFS=,; echo "${settle[*]}") again to settle them." >&2
    fi
    if ((!undone)); then
        # shellcheck disable=SC2034  # read by the EXIT trap
        KEEP_WORK=1
        echo "Putting the previous keys back failed too. Copies of the files they were in are in $work (owner only); details in $LOG." >&2
    elif ((${#kept_new[@]})); then
        echo "Every other key is back." >&2
    elif ((healthy)); then
        echo "The previous keys are back and the stack is healthy." >&2
    else
        echo "The previous keys are back, but the stack is not healthy; check status." >&2
    fi
    exit 1
}

wait_for_postgres
mkdir -p "$BACKUP_DIR"
MIN_FREE_GB=${MIN_FREE_GB:-5}
db_bytes=$($COMPOSE exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -Atc "SELECT pg_database_size(current_database())" 2>>"$LOG" < /dev/null)
free_kb=$(df -Pk "$BACKUP_DIR" | awk 'NR==2 {print $4}')
[[ $db_bytes =~ ^[0-9]+$ && $free_kb =~ ^[0-9]+$ ]] || { echo "could not determine the database size or free disk; not rotating" >&2; exit 1; }
need_kb=$(( db_bytes / 1024 + MIN_FREE_GB * 1024 * 1024 ))
if (( free_kb < need_kb )); then
    echo "Not enough disk: $((free_kb / 1024)) MiB free in $BACKUP_DIR, need $((need_kb / 1024)) MiB (database size + MIN_FREE_GB=$MIN_FREE_GB GiB); not rotating" >&2
    exit 1
fi

log "Keeping a fresh dump at $dump"
$COMPOSE exec -T postgres pg_dump -U "$DB_USER" -Fc "$DB_NAME" > "$dump.partial"
[[ -s $dump.partial ]] || { echo "fresh dump is empty; not rotating" >&2; exit 1; }
mv "$dump.partial" "$dump"

echo "started $STAMP" > "$ROTATE_MARKER"
trap rotate_failed ERR
STEP=stopping
log "Stopping app services"
STOPPED=1
$COMPOSE stop "${APP_SERVICES[@]}"

STEP=keeping
# The new value too, before anything depends on it: if this run dies or its
# undo fails, it is still known, wherever the live copy ends up.
for name in "${rotate[@]}"; do
    in_list "$name" "$KEPT" || continue
    log "Keeping $name for the backups that need it"
    keep_key "$name" "$STAMP-0" "$(old "$name")"
    keep_key "$name" "$STAMP-1" "$(new "$name")"
done

STEP=writing
log "Writing the new values"
save_stores
for name in "${rotate[@]}"; do
    store=STORE_$name
    read -r kind path <<<"${!store}"
    write_value "$name" "$kind" "$path" "$(new "$name")"
done
if [[ -n $SSM ]]; then
    for name in "${rotate[@]}"; do
        in_list "$name" "$IN_SSM" || continue
        SSM_WRITTEN+=("$name")
        ssm_put "$SSM/$(lower "$name")" "$(new "$name")"
    done
fi

if in_list POSTGRES_PASSWORD "${rotate[*]}"; then
    STEP=postgres
    log "Changing the Postgres password"
    PG_ROLE=unknown
    set_db_password "$(new POSTGRES_PASSWORD)"
    PG_ROLE=new
fi
if in_list SECRETS_ENCRYPTION_KEY "${rotate[*]}"; then
    STEP=secrets
    log "Re-encrypting the stored secrets"
    DB_KEY=unknown
    rekey_secrets "$LOG" "$(new SECRETS_ENCRYPTION_KEY)" "$(old SECRETS_ENCRYPTION_KEY)"
    DB_KEY=new
fi

STEP=starting
log "Recreating the containers"
$COMPOSE up -d --force-recreate
wait_healthy
trap - ERR
rm -f "$ROTATE_MARKER"
log "Rotated ${rotate[*]}; the fresh dump is $dump"
if in_list SECRETS_ENCRYPTION_KEY "${rotate[*]}"; then
    echo "Transfer and password reset links and OAuth sign-ins started before now no longer work."
fi
shopt -s nullglob
dumps=("$BACKUP_DIR"/pre-rotate-*.dump)
excess=$(( ${#dumps[@]} - KEEP_BACKUPS ))
(( excess <= 0 )) || rm -f "${dumps[@]:0:excess}"
