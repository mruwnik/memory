#!/usr/bin/env bash
# Prints the compose project of the existing stack at checkout DIR (default:
# $PWD), for pinning COMPOSE_PROJECT_NAME in .env.
#
#   tools/compose_project.sh [DIR]
#
# Compose names a project after the directory it runs in, and a symlinked
# checkout has two names (EC2: /opt/memory -> /srv/memory/app), so the name
# comes only from the stack itself. It is the one project whose containers'
# working dir is DIR (symlinks resolved on both sides), and only if that
# project owns a <project>_db_data volume or no other project owns one.
# Anything else fails, asking the operator to set the name by hand.
#
# Env: DOCKER (default docker).
set -Eeuo pipefail

DIR=${1:-$PWD}
DOCKER=${DOCKER:-docker}

refuse() {
    echo "$*" >&2
    echo "Set COMPOSE_PROJECT_NAME in .env to the project that owns this checkout's data (docker volume ls), then re-run." >&2
    exit 1
}

# docker ps/volume ls output, or a refusal naming the failed query.
query() {
    local out
    out=$("$DOCKER" "$@" 2>&1) || refuse "Could not ask $DOCKER for the compose project ($DOCKER $1 failed: ${out:0:200})."
    printf '%s\n' "$out"
}

containers=$(query ps -a --filter label=com.docker.compose.project \
    --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.project.working_dir"}}')
volumes=$(query volume ls --filter label=com.docker.compose.project \
    --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.volume"}}')

here=$(cd "$DIR" && pwd -P)
candidates=$(while read -r project dir; do
    if [[ -n $project && -d $dir && $(cd "$dir" && pwd -P) == "$here" ]]; then echo "$project"; fi
done <<<"$containers" | sort -u)
owners=$(awk '$2 == "db_data" {print $1}' <<<"$volumes" | sort -u)
db_volumes=$(awk '$2 == "db_data" {print $1 "_db_data"}' <<<"$volumes" | sort -u | tr '\n' ' ')
found="Projects with containers in $DIR: $(tr '\n' ' ' <<<"${candidates:-none}"). db_data volumes: ${db_volumes:-none}"

[[ -n $candidates && $(wc -l <<<"$candidates") -eq 1 ]] || refuse "No single compose project runs from $DIR. $found"
others=$(grep -vxF -- "$candidates" <<<"$owners" | grep . || true)
if [[ -n $others ]] && ! grep -qxF -- "$candidates" <<<"$owners"; then
    refuse "Project $candidates has no db_data volume, but another project does. $found"
fi
echo "$candidates"
