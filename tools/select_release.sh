#!/usr/bin/env bash
# Print the release tag to install, or fail. Run inside the repo checkout.
#
#   tools/select_release.sh [--require-signed ALLOWED_SIGNERS] [--min-age-hours N] [--no-record] [REF]
#
# Without REF, the newest vMAJOR.MINOR.PATCH tag that passes the checks. With
# REF, that v* tag if it passes (a non-v* ref is refused when a check is on). --require-signed accepts only annotated tags
# with a valid SSH signature from a key in the allowed_signers file whose
# embedded name matches the ref. --min-age-hours accepts only tags at least that
# old: the later of the tag date and when this host first saw the tag object, so
# a signer can't backdate a tag past the delay. Used by update.sh, the nightly
# timer and the update-to action. First sightings are recorded unless --no-record
# (dry runs) or no check is requested; an unrecorded tag counts as first seen now.
#
# Exits 3 ("up to date" on stderr) when automatic selection finds the newest
# qualifying tag is HEAD or an ancestor of it, so the timer never downgrades.
#
# Env: STATE_DIR (default /var/lib/memory) holds seen-tags, "<tag sha> <epoch>" lines,
#      and skip-tags, tags that automatic selection passes over (see tools/ops/rollback.sh).
set -Eeuo pipefail

STATE_DIR=${STATE_DIR:-/var/lib/memory}
SEEN=$STATE_DIR/seen-tags
SKIP=$STATE_DIR/skip-tags

ALLOWED=""
MIN_AGE_HOURS=0
REF=""
RECORD=1
while (($#)); do
    case "$1" in
        --require-signed) ALLOWED=${2:?--require-signed needs a file}; shift 2 ;;
        --min-age-hours) MIN_AGE_HOURS=${2:?--min-age-hours needs a number}; shift 2 ;;
        --no-record) RECORD=0; shift ;;
        -h|--help) sed -n "2,20p" "$0"; exit 0 ;;
        -*) echo "unknown option: $1" >&2; exit 2 ;;
        *) REF=$1; shift ;;
    esac
done

if ! [[ $MIN_AGE_HOURS =~ ^[0-9]+$ ]]; then
    echo "--min-age-hours must be a non-negative integer, got '$MIN_AGE_HOURS'" >&2
    exit 2
fi
if [[ -n $ALLOWED && ! -s $ALLOWED ]]; then
    echo "allowed signers file missing or empty: $ALLOWED" >&2
    exit 2
fi

# Remembers when each tag object first appeared; only ever adds entries.
record_seen() {
    mkdir -p "$STATE_DIR"
    touch "$SEEN"
    local sha now
    now=$(date +%s)
    git for-each-ref --format='%(objectname)' 'refs/tags/v*' |
        while read -r sha; do
            grep -q "^$sha " "$SEEN" || echo "$sha $now"
        done >> "$SEEN"
}

# Prints why a tag fails and returns nonzero, so an explicit REF can say why.
check_tag() {
    local tag=$1 ref type name sha created seen now
    ref=refs/tags/$tag
    read -r _ type sha created name < <(git for-each-ref \
        --format='%(refname) %(objecttype) %(objectname) %(creatordate:unix) %(tag)' "$ref" |
        awk -v r="$ref" '$1 == r')
    if [[ -n $ALLOWED ]]; then
        # Lightweight tags can't be signed, and a signed object republished
        # under another name would otherwise pass for a newer release.
        if [[ $type != tag || $name != "$tag" ]]; then
            echo "$tag is not an annotated tag of that name" >&2
            return 1
        fi
        # The program overrides keep an OpenPGP or X.509 signature from being
        # checked against the host's keyring instead of ALLOWED.
        if ! git -c gpg.format=ssh -c "gpg.ssh.allowedSignersFile=$ALLOWED" \
                -c gpg.program=false -c gpg.x509.program=false \
                verify-tag "$ref" > /dev/null 2>&1; then
            echo "$tag is not signed by an allowed signer" >&2
            return 1
        fi
    fi
    now=$(date +%s)
    seen=$(awk -v s="$sha" '$1 == s {print $2; exit}' "$SEEN" 2> /dev/null || true)
    seen=${seen:-$now}
    created=$(( seen > created ? seen : created ))
    if (( now - created < MIN_AGE_HOURS * 3600 )); then
        echo "$tag is younger than $MIN_AGE_HOURS hours" >&2
        return 1
    fi
}

CHECKING=0
[[ -n $ALLOWED || $MIN_AGE_HOURS -gt 0 ]] && CHECKING=1
((CHECKING && RECORD)) && record_seen

if [[ -n $REF ]]; then
    if ((CHECKING)); then
        # Only v* tags are recorded, and any other tag would fall back to the
        # signer's own date.
        if [[ $REF != v* ]] || ! git show-ref --verify --quiet "refs/tags/$REF"; then
            echo "$REF is not a v* tag, so it can't be verified" >&2
            exit 1
        fi
        check_tag "$REF"
    fi
    echo "$REF"
    exit 0
fi

while read -r tag; do
    # Rolled back from; update.sh forgets a tag once it is installed on request.
    grep -qxF -- "$tag" "$SKIP" 2> /dev/null && continue
    if check_tag "$tag" 2> /dev/null; then
        # Never downgrade: the newest qualifying tag is already installed.
        if git merge-base --is-ancestor "refs/tags/$tag^{commit}" HEAD 2> /dev/null; then
            echo "up to date: $tag is installed or older than the checkout" >&2
            exit 3
        fi
        echo "$tag"
        exit 0
    fi
done < <(git tag --list 'v*' --sort=-v:refname | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' || true)
echo "no release tag is signed by an allowed signer and at least $MIN_AGE_HOURS hours old" >&2
exit 1
