#!/usr/bin/env bash
# Cut a release: an SSH-signed annotated tag on master. See UPDATING.md.
#
#   tools/release.sh [--push] vX.Y.Z
#
# Requires a clean tree on master, a "## vX.Y.Z" section in CHANGELOG.md with a
# non-empty Changes list, and passing tests. The tag message is that section.
# Pushes master and the tag only with --push.
#
# Env: SIGNING_KEY (SSH key to sign with; default git's user.signingkey),
#      REMOTE (default origin), RELEASE_TEST_CMD
#      (default "~/.virtualenvs/memory/bin/pytest --run-slow -n 3 --dist loadfile").
set -Eeuo pipefail

cd "$(dirname "$0")/.."

REMOTE=${REMOTE:-origin}
RELEASE_TEST_CMD=${RELEASE_TEST_CMD:-$HOME/.virtualenvs/memory/bin/pytest --run-slow -n 3 --dist loadfile}
PUSH=0
VERSION=""
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=1 ;;
        -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
        -*) echo "unknown option: $arg" >&2; exit 2 ;;
        *) VERSION=$arg ;;
    esac
done

die() { echo "$*" >&2; exit 1; }

# The body of version $1's CHANGELOG.md section: from its heading to the next
# "## " heading.
changelog_section() {
    awk -v v="$1" '/^## / { if (found) exit; found = ($2 == v); next } found' CHANGELOG.md
}

# The bullets of the "### Changes" subsection of a section body on stdin.
changes_list() {
    awk '/^### / { in_changes = ($0 == "### Changes"); next } in_changes && /^- /'
}

[[ $VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: tools/release.sh [--push] vX.Y.Z"
[[ $(git rev-parse --abbrev-ref HEAD) == master ]] || die "Releases are cut from master."
[[ -z $(git status --porcelain --untracked-files=no) ]] || die "Tracked files have local changes; commit or discard them first."
git fetch -q "$REMOTE" master
[[ $(git rev-parse HEAD) == "$(git rev-parse FETCH_HEAD)" ]] || die "Local master differs from $REMOTE/master; push or pull first."
git rev-parse -q --verify "refs/tags/$VERSION" > /dev/null && die "Tag $VERSION already exists."
notes=$(changelog_section "$VERSION")
[[ -n $notes ]] || die "CHANGELOG.md has no '## $VERSION' section; rename Unreleased first."
[[ -n $(changes_list <<< "$notes") ]] || die "CHANGELOG.md's $VERSION section lists no Changes."

echo "==> Running tests"
$RELEASE_TEST_CMD || die "tests failed; no tag created."

# Build an archive so ignored or untracked local files cannot mask missing
# release sources. Dependencies are installed independently of the working tree.
echo "==> Building the committed frontend"
release_check_dir=$(mktemp -d)
trap 'rm -rf "$release_check_dir"' EXIT
git archive HEAD frontend | tar -x -C "$release_check_dir"
(
    cd "$release_check_dir/frontend" &&
        npm ci --no-audit --no-fund &&
        npm run build
) || die "committed frontend build failed; no tag created."

echo "==> Tagging $VERSION"
git -c gpg.format=ssh ${SIGNING_KEY:+-c "user.signingkey=$SIGNING_KEY"} \
    tag -s --cleanup=whitespace -m "Release $VERSION" -m "$notes" "$VERSION"

if [[ $PUSH == 1 ]]; then
    git push "$REMOTE" master "$VERSION"
else
    echo "Tagged locally. To publish: git push $REMOTE master $VERSION"
fi
