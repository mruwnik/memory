#!/usr/bin/env bash
# Print the last error records of the api and worker containers, cut down to
# what the operator may read.
#
#   tools/ops/logs.sh [LINES]   (per service; default 100, at most 1000)
set -Eeuo pipefail
declare -F parse_args >/dev/null || source "$(dirname "$0")/lib.sh"
parse_args "$@"

lines=${POSITIONAL[0]:-100}
if ! [[ $lines =~ ^[1-9][0-9]{0,3}$ ]] || ((lines > 1000)); then
    echo "LINES must be a number from 1 to 1000" >&2
    exit 2
fi

# The app's error logs are audited not to carry item text, queries or keys, and
# this is the second line of defence. It keeps only ERROR and CRITICAL record
# lines (in the Celery/app or uvicorn format) and the shape of tracebacks: frame
# locations and exception types. Exception messages, frame source lines and
# every other line are dropped, because they quote whatever data was at hand.
# Kept lines lose quoted values, URL paths and queries, emails, credentials and
# token-shaped strings, and are cut to 300 characters.
filter=$(cat <<'PY'
import io, re, sys

# Celery's and the app's "[time: LEVEL/name] ", and uvicorn's padded "LEVEL: ".
LEVEL = re.compile(r"\[\d{4}-\d\d-\d\d [\d:,]+: (ERROR|CRITICAL)/[\w.-]+\] |ERROR: {4}\S|CRITICAL: \S")
HEADERS = (
    "Traceback (most recent call last):",
    "During handling of the above exception, another exception occurred:",
    "The above exception was the direct cause of the following exception:",
)
FRAME = re.compile(r'  File "[^"]+", line \d+, in [\w<>.]+')
EXCEPTION_TYPE = re.compile(r"[A-Za-z_][\w.]*(?=:|$)")
SCRUB = [
    (re.compile(r"([a-z][a-z0-9+.-]*://)([^/\s@'\"]*@)?([^/\s?#'\"]*)[^\s'\"]*", re.I), r"\1\3"),
    # Reprs escape quotes with backslashes; an unclosed quote runs to the end;
    # an apostrophe inside a word ("can't") is never a quote boundary.
    (re.compile(r"\"(\\.|[^\"\\])*(\"|$)|(?<!\w)'(\\.|'(?=\w)|[^'\\])*?('(?!\w)|$)"), "'***'"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "<email>"),
    (re.compile(r"\b(bearer|basic)\s+\S+", re.I), r"\1 ***"),
    (re.compile(r"\b([\w-]*(token|secret|passw(or)?d|key|auth\w*|cookie|signature|credential|dsn)[\w-]*\s*[=:]\s*)[^\s,;&)]+", re.I), r"\1***"),
    (re.compile(r"(?=[\w+/=-]*\d)(?=[\w+/=-]*[A-Za-z])[\w+/=-]{24,}"), "***"),
]


def scrub(text):
    for pattern, replacement in SCRUB:
        text = pattern.sub(replacement, text)
    return text


def keep(lines):
    in_traceback = False
    for line in lines:
        ts, _, text = line.rstrip("\n").partition(" ")
        if LEVEL.match(text):
            in_traceback = False
            yield ts, scrub(text)
        elif text in HEADERS:
            in_traceback = True
            yield ts, text
        elif not in_traceback or not text:
            continue
        elif FRAME.fullmatch(text):
            yield ts, text
        elif not text.startswith(" "):
            # The exception line closes the traceback.
            in_traceback = False
            exception = EXCEPTION_TYPE.match(text)
            if exception:
                yield ts, exception.group()


stdin = io.TextIOWrapper(sys.stdin.buffer, errors="replace")
for ts, text in list(keep(stdin))[-int(sys.argv[1]):]:
    print(ts, text[:300])
PY
)

for service in api worker; do
    echo "[$service]"
    $COMPOSE logs --no-color --no-log-prefix --timestamps --tail 20000 "$service" 2>/dev/null \
        | python3 -I -c "$filter" "$lines" 2>/dev/null \
        || echo "could not read the $service logs"
done
