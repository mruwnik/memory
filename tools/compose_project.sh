#!/usr/bin/env bash
# Prints the compose project that owns the checkout at DIR (default: $PWD as
# invoked), for pinning COMPOSE_PROJECT_NAME in .env.
#
#   tools/compose_project.sh [DIR]
#
# Compose names a project after the directory it runs in, and a symlinked
# checkout has two names (EC2: /opt/memory -> /srv/memory/app), so the name is
# never guessed while data may exist under another one:
#   1. containers whose compose working dir resolves to DIR: their project;
#   2. else exactly one project with a db_data volume: that project;
#   3. else no compose volumes at all (a fresh host): DIR's name.
# Anything else fails, asking the operator to set the name by hand.
#
# Env: DOCKER (default docker).
set -Eeuo pipefail

DIR=${1:-$PWD}
DOCKER=${DOCKER:-docker}

refuse() {
    echo "$*" >&2
    echo "Set COMPOSE_PROJECT_NAME in .env to the project that owns your volumes (docker volume ls), then re-run." >&2
    exit 1
}

# docker ps/volume ls output, or a refusal naming the failed query.
query() {
    local out
    out=$("$DOCKER" "$@" 2>&1) || refuse "Could not ask $DOCKER for the compose project ($DOCKER $1 failed: ${out:0:200})."
    printf '%s\n' "$out"
}

if ! command -v "$DOCKER" > /dev/null; then
    containers="" volumes=""
else
    containers=$(query ps -a --filter label=com.docker.compose.project \
        --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.project.working_dir"}}')
    volumes=$(query volume ls --filter label=com.docker.compose.project \
        --format '{{.Label "com.docker.compose.project"}} {{.Label "com.docker.compose.volume"}}')
fi

here=$(cd "$DIR" && pwd -P)
names=$(while read -r project dir; do
    if [[ -n $project && -d $dir && $(cd "$dir" && pwd -P) == "$here" ]]; then echo "$project"; fi
done <<<"$containers" | sort -u)
if [[ -z $names ]]; then
    names=$(awk '$2 == "db_data" {print $1}' <<<"$volumes" | sort -u)
    if [[ -z $names ]]; then
        [[ -z ${volumes//[[:space:]]/} ]] || refuse "No containers run from $DIR and no project has a db_data volume, but compose volumes exist."
        names=$(basename "$DIR" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-' | sed 's/^[_-]*//')
        [[ -n $names ]] || refuse "The name of $DIR is not a usable compose project name."
    fi
fi
(( $(wc -l <<<"$names") == 1 )) || refuse "Several compose projects could own $DIR: $(tr '\n' ' ' <<<"$names")"
echo "$names"
