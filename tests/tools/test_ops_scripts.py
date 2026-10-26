import io
import os
import pathlib
import re
import shutil
import stat
import subprocess
import tarfile
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
KEY = "backup-passphrase-1"
SECRETS = {
    "POSTGRES_PASSWORD": "pg-secret-4f9a",
    "JWT_SECRET": "jwt-secret-77c1",
    "VOYAGE_API_KEY": "voyage-secret-0b3e",
}

# FAKE_FAIL is a comma list of steps that exit 1: pg_restore, psql, health, createdb,
# qdrant_restore (the one-off qdrant run), qdrant_check (the final collections
# check), fernet (the worker file restore), rekey (the first re-encryption of the
# stored secrets), rekey_back (every later one). FAKE_DB_BYTES is the database size.
FAKE_COMPOSE = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
fails() { [[ ,${FAKE_FAIL:-}, == *,$1,* ]]; }
case "$*" in
    *"python -c"*"/health"*)
        fails health && exit 1
        printf 'status=healthy\\nversion=v1\\ndb_revision=r1\\nexpected_revision=r1\\n'
        printf 'item_text=%s\\n' "$(grep -h '^JWT_SECRET=' .env | cut -d= -f2)"
        ;;
    *pg_dump*) echo DUMPDATA ;;
    "logs "*) cat "$FAKE_LOGS/${@: -1}" 2>/dev/null ;;
    "ps -a -q") echo container1 ;;
    "config --services") printf 'api\\nworker\\npostgres\\n' ;;
    *pg_restore*) cat >/dev/null; echo "row: secret-row" >&2; fails pg_restore && exit 1 ;;
    *pg_database_size*) echo "${FAKE_DB_BYTES:-1000}" ;;
    *psql*) cat >> "$FAKE_LOG.psql"; echo "row: secret-row" >&2; fails psql && exit 1 ;;
    *createdb*) fails createdb && exit 1 ;;
    *"exec -T qdrant bash -c"*)
        method=${@: -2:1}; path=${@: -1}
        [[ $path == /collections && $method == GET ]] && fails qdrant_check && exit 1
        printf 'HTTP/1.0 200 OK\r\n\r\n'
        if [[ $path == /collections && ${FAKE_COLLECTIONS:-} == empty ]]; then echo '{"result":{"collections":[]}}'
        elif [[ $path == /collections ]]; then echo '{"result":{"collections":[{"name":"demo"}]}}'
        else echo '{}'; fi
        ;;
    *"--entrypoint tar qdrant -czf"*) tar -cz -T /dev/null; fails qdrant_tar && exit 1 ;;
    *"--entrypoint tar qdrant -xzf"*) cat > "$FAKE_LOG.untar"; fails untar && exit 1 ;;
    "stop qdrant") fails qdrant_stop && exit 1 ;;
    *"--entrypoint find qdrant"*) ;;
    *"--storage-snapshot"*)
        fails qdrant_restore && exit 1
        for a in "$@"; do [[ $a == *:/restore/full.snapshot:ro ]] && cat "${a%%:*}" > "$FAKE_LOG.snapshot"; done
        ;;
    *"memory.common.db.rekey"*)
        echo "to=${ROTATE_SECRETS_TO:-} from=$(tr '\\n' , <<<"${ROTATE_SECRETS_FROM:-}")" >> "$FAKE_LOG.rekey"
        runs=$(wc -l < "$FAKE_LOG.rekey")
        { fails rekey && ((runs == 1)); } || { fails rekey_back && ((runs > 1)); } && exit 1
        echo "secrets_reencrypted=${FAKE_REKEYED:-3}" ;;
    *"--entrypoint python worker"*) cat >> "$FAKE_LOG.fernet"; fails fernet && exit 1 ;;
esac
exit 0
"""
FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$FAKE_LOG"
case "$1" in
    inspect) echo "api: running restarts=0" ;;
    exec) printf 'HTTP/1.0 200 OK\r\n\r\n{}\n' ;;
esac
"""
FAKE_CHOWN = """#!/usr/bin/env bash
echo "chown $*" >> "$FAKE_LOG"
"""
FAKE_UPDATE = """#!/usr/bin/env bash
echo "update.sh $* lock_held=${MEMORY_LOCK_HELD:-}" >> "$FAKE_LOG"
echo "==> Updating v1 -> $4"
echo "ERROR: Key (email)=(someone@example.com) already exists"
echo "UPDATE RESULT: ok (exit 0)"
"""
# FAKE_UNITS: the timers that exist; FAKE_FAILED: units that have failed.
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
case "$1" in
    is-enabled) [[ ,${FAKE_UNITS:-}, == *,$2,* ]] && echo enabled || { echo not-found; exit 1; } ;;
    is-failed) [[ ,${FAKE_FAILED:-}, == *,$3,* ]] ;;
esac
"""
FAKE_SHUTDOWN = """#!/usr/bin/env bash
echo "shutdown $*" >> "$FAKE_LOG"
"""
# Maps s3://bucket/prefix/<rest> onto $FAKE_S3/<rest>, and SSM parameter <name>
# onto $FAKE_SSM<name> (its KMS key in $FAKE_SSM.kms<name>). FAKE_FAIL=ssm_put fails writes,
# ssm_read reads by path.
FAKE_AWS = """#!/usr/bin/env bash
echo "aws $*" >> "$FAKE_LOG"
local_path() { echo "$FAKE_S3/${1#s3://bucket/pfx/}"; }
case "$2" in
    put-parameter)
        [[ ,${FAKE_FAIL:-}, == *,ssm_put,* ]] && exit 1
        python3 -c 'import json, os, sys
p = json.load(open(sys.argv[1]))
dest = os.environ["FAKE_SSM"] + p["Name"]
kms = os.environ["FAKE_SSM"] + ".kms" + p["Name"]
for path, value in [(dest, p["Value"]), (kms, p["KeyId"])]:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(value)' "${4#file://}" ;;
    get-parameters-by-path)
        [[ ,${FAKE_FAIL:-}, == *,ssm_read,* ]] && exit 1
        for f in "$FAKE_SSM$4"/*; do
            [[ -f $f ]] || continue
            printf '%s\\t' "$(cat "$f")"
        done
        echo ;;
    ls) p=$(local_path "$3"); [[ -d $p ]] || exit 1
        for f in "$p"/*; do
            [[ -d $f ]] && echo "                           PRE $(basename "$f")/" \\
                || echo "2025-01-02 03:04:05        123 $(basename "$f")"
        done ;;
    cp) cat "$(local_path "$3")" ;;
    sync) mkdir -p "$4"; cp -a "$(local_path "$3")/." "$4/" ;;
esac
"""


def git(cwd, *args):
    return subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def box(tmp_path, lock_bin):
    """A repo with tools/ops, fake compose/docker/aws/shutdown, and a secret-laden .env."""
    repo = tmp_path / "repo"
    (repo / "tools").mkdir(parents=True)
    shutil.copytree(REPO_ROOT / "tools" / "ops", repo / "tools" / "ops")
    (repo / ".env").write_text("".join(f"{k}={v}\n" for k, v in SECRETS.items()))
    bin_dir = lock_bin
    for name, body in {
        "compose": FAKE_COMPOSE,
        "docker": FAKE_DOCKER,
        "shutdown": FAKE_SHUTDOWN,
        "aws": FAKE_AWS,
        "chown": FAKE_CHOWN,
        "systemctl": FAKE_SYSTEMCTL,
        "df": "#!/bin/sh\nprintf 'Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 20971520 1048576 19922944 5%% /\\n'\n",
    }.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (repo / "tools" / "update.sh").write_text(FAKE_UPDATE)
    (repo / "tools" / "update.sh").chmod(0o755)
    git(repo, "init", "--quiet")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "v1")
    git(repo, "tag", "v1")
    return repo


def ops(box, script, *args, **env):
    tmp = box.parent
    result = subprocess.run(
        ["bash", f"tools/ops/{script}.sh", *args],
        cwd=box,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{tmp / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(tmp / "calls.log"),
            "FAKE_S3": str(tmp / "s3"),
            "FAKE_SSM": str(tmp / "ssm"),
            "COMPOSE": str(tmp / "bin" / "compose"),
            "DOCKER": str(tmp / "bin" / "docker"),
            "OPS_ENV": str(tmp / "no-ops.env"),
            "STATE_DIR": str(tmp / "state"),
            "LOCK_FILE": str(tmp / "nightly.lock"),
            "HEALTH_TIMEOUT": "10",
            "HEALTH_POLL": "0.2",
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr


def calls(box):
    log = box.parent / "calls.log"
    return log.read_text() if log.exists() else ""


def state(box, name):
    return box.parent / "state" / name


def make_dumps(box, names):
    (box / "backups").mkdir(exist_ok=True)
    for name in names:
        (box / "backups" / name).write_text("dump")


def encrypt(plain: bytes, dest: pathlib.Path):
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(
        subprocess.run(
            ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"pass:{KEY}"],
            input=plain,
            capture_output=True,
            check=True,
        ).stdout
    )


def snapshot_tar(label: str) -> bytes:
    """A stand-in for a Qdrant full snapshot: a tar of per-collection snapshots."""
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w") as tar:
        data = f"collection {label}".encode()
        info = tarfile.TarInfo(f"demo-{label}.snapshot")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return out.getvalue()


def gzip_bytes(plain: bytes) -> bytes:
    return subprocess.run(["gzip", "-c"], input=plain, capture_output=True, check=True).stdout


@pytest.fixture
def backup_set(tmp_path):
    """An S3-shaped tree: a complete 2025-01-02 database set, plus file backups."""
    root = tmp_path / "s3"
    encrypt(gzip_bytes(b"OLD SQL"), root / "databases/postgres-20250101.sql.gz.enc")
    encrypt(snapshot_tar("old"), root / "databases/qdrant-20250101.snapshot.enc")
    encrypt(gzip_bytes(b"NEW SQL"), root / "databases/postgres-20250102.sql.gz.enc")
    encrypt(snapshot_tar("new"), root / "databases/qdrant-20250102.snapshot.enc")
    return root


def add_file_backups(box, root):
    src = box.parent / "src"
    (src / "emails").mkdir(parents=True)
    (src / "emails" / "a.eml").write_text("mail")
    tar = subprocess.run(
        ["tar", "-cz", "-C", str(src), "emails"], capture_output=True, check=True
    ).stdout
    encrypt(tar, root / "emails.tar.gz.ssl")
    (root / "notes.tar.gz.enc").write_bytes(b"fernet-bytes")  # Fernet, decrypted in the worker
    (root / "ebooks").mkdir()
    (root / "ebooks" / "b.epub").write_text("book")


RESTORE_ENV = {"BACKUP_ENCRYPTION_KEY": KEY}


def test_status_never_prints_anything_but_whitelisted_health_keys(box):
    code, output = ops(box, "status")

    assert code == 0, output
    assert "status=healthy" in output
    assert "api: running restarts=0" in output
    for secret in SECRETS.values():
        assert secret not in output
    assert "item_text" not in output


def test_status_reports_update_failure_and_backup_markers(box):
    state_dir = box.parent / "state"
    state_dir.mkdir()
    (state_dir / "update-failed").write_text("v2 something sensitive")
    (state_dir / "last-backup").write_text("failed row text")
    (state_dir / "last-update-check").write_text("failed 2026-01-01T00:00:00Z")

    code, output = ops(box, "status")

    assert code == 0, output
    assert "update_failed=yes" in output
    assert "last_backup=failed" in output
    assert "last_update_check=failed" in output
    assert "something sensitive" not in output
    assert "row text" not in output


def test_status_reports_a_degraded_api_instead_of_unreachable(box):
    code, output = ops(box, "status", FAKE_FAIL="health")

    assert code == 0, output
    assert "status=unreachable" in output


TS = "2026-10-24T10:00:00.000000000Z"
ERROR = "[2026-10-24 10:00:02,000: ERROR/memory.api.search]"
ITEM_TEXT = "Dear Alice the merger is off"
QUERY = "what is my salary"
API_KEY = "sk-ant-api03-a1b2c3d4e5f6g7h8i9j0"
API_LOG = [
    f'INFO:     172.18.0.1:5000 - "GET /search?q={QUERY} HTTP/1.1" 200 OK',
    f"[2026-10-24 10:00:01,000: WARNING/memory.api.search] slow search: {QUERY}",
    f"{ERROR} Embedding failed: Client error '401' for url 'https://api.example.com/v1/embed?key={API_KEY}'",
    "Traceback (most recent call last):",
    '  File "/app/src/memory/common/embedding.py", line 140, in embed',
    f'    vectors = client.embed("{QUERY}")',
    "              ^^^^^^^^^^^^^^^^^^^^^^^^^",
    f"sqlalchemy.exc.IntegrityError: (psycopg.errors.UniqueViolation) Key (body)=({ITEM_TEXT}) exists",
    f"DETAIL:  Key (body)=({ITEM_TEXT}) already exists.",
    f"[parameters: {{'body': '{ITEM_TEXT}'}}]",
    "",
    "The above exception was the direct cause of the following exception:",
    "",
    "Traceback (most recent call last):",
    '  File "/app/src/memory/api/search/search.py", line 12, in <module>',
    f"    raise RuntimeError({QUERY!r})",
    f"RuntimeError: {QUERY}",
    "ERROR:    Exception in ASGI application",
    f"ERROR: Key (body)=({ITEM_TEXT}) already exists",
    f"[draft: ERROR: {ITEM_TEXT}]",
    f"[2026-10-24 10:00:04,000: INFO/memory.api] [2026-10-24 10:00:04,000: ERROR/x] {ITEM_TEXT}",
    "CRITICAL: Application startup failed",
]
WORKER_LOG = [
    "[2026-10-24 10:00:05,000: ERROR/ForkPoolWorker-1] Task memory.workers.tasks.email.sync_account"
    f"[0b1c2d3e-4f5a-6b7c-8d9e-0f1a2b3c4d5e] raised unexpected: ValueError('{ITEM_TEXT}')",
    f"[2026-10-24 10:00:06,000: INFO/ForkPoolWorker-1] Processed {ITEM_TEXT}",
]


def write_logs(box, **services):
    logs = box.parent / "logs"
    logs.mkdir(exist_ok=True)
    for service, lines in services.items():
        (logs / service).write_text("".join(f"{TS} {line}\n" for line in lines))
    return str(logs)


def test_logs_keeps_only_error_records_and_the_shape_of_tracebacks(box):
    code, output = ops(box, "logs", FAKE_LOGS=write_logs(box, api=API_LOG, worker=WORKER_LOG))

    assert code == 0, output
    assert output.splitlines() == [
        "[api]",
        f"{TS} {ERROR} Embedding failed: Client error '***' for url '***'",
        f"{TS} Traceback (most recent call last):",
        f'{TS}   File "/app/src/memory/common/embedding.py", line 140, in embed',
        f"{TS} sqlalchemy.exc.IntegrityError",
        f"{TS} The above exception was the direct cause of the following exception:",
        f"{TS} Traceback (most recent call last):",
        f'{TS}   File "/app/src/memory/api/search/search.py", line 12, in <module>',
        f"{TS} RuntimeError",
        f"{TS} ERROR:    Exception in ASGI application",
        f"{TS} CRITICAL: Application startup failed",
        "[worker]",
        f"{TS} [2026-10-24 10:00:05,000: ERROR/ForkPoolWorker-1] Task memory.workers.tasks.email.sync_account"
        "[***] raised unexpected: ValueError('***')",
        "[slack-sync-worker]",
    ]


@pytest.mark.parametrize(
    "message,expected",
    [
        ("login failed password=hunter2 for x", "login failed password=*** for x"),
        ("refresh failed, token: abc123", "refresh failed, token: ***"),
        ("rejected Bearer abcdef", "rejected Bearer ***"),
        ("mail to alice@example.com bounced", "mail to <email> bounced"),
        ("GET https://u:pw@host.example:8443/s3cr3t/x?token=t failed", "GET https://host.example:8443 failed"),
        (f"key {API_KEY} rejected", "key *** rejected"),
        (f"note \"{ITEM_TEXT}\" and 'it''s' failed", "note '***' and '***''***' failed"),
        ("raised ValueError('it\\'s a \"private\" note about Bob\\'s divorce')", "raised ValueError('***')"),
        (f"note '{ITEM_TEXT}", "note '***'"),
        ("Couldn't fetch 'Bob divorce notes' from drive", "Couldn't fetch '***' from drive"),
        ("no 'Bob's divorce' here", "no '***' here"),
        ("'utf-8' codec can't decode", "'***' codec can't decode"),
        ("Couldn't parse it", "Couldn't parse it"),
        ("x" * 400, ("x" * 400)[: 300 - len(ERROR) - 1]),
    ],
    ids=["password", "token", "bearer", "email", "url", "api-key", "quoted", "escaped-quotes", "unterminated",
         "in-word-apostrophe", "apostrophe-inside-quote", "apostrophe-after-quote", "apostrophe-only", "long"],
)
def test_logs_scrubs_error_messages(box, message, expected):
    code, output = ops(box, "logs", FAKE_LOGS=write_logs(box, api=[f"{ERROR} {message}"]))

    assert code == 0, output
    assert output.splitlines() == ["[api]", f"{TS} {ERROR} {expected}", "[worker]", "[slack-sync-worker]"]


def test_logs_prints_the_last_lines_per_service(box):
    errors = [f"{ERROR} failure {i}" for i in range(5)]

    code, output = ops(box, "logs", "2", FAKE_LOGS=write_logs(box, api=errors, worker=errors))

    assert code == 0, output
    assert output.splitlines() == [
        "[api]", f"{TS} {ERROR} failure 3", f"{TS} {ERROR} failure 4",
        "[worker]", f"{TS} {ERROR} failure 3", f"{TS} {ERROR} failure 4",
        "[slack-sync-worker]",
    ]  # fmt: skip


@pytest.mark.parametrize("lines", ["0", "1001", "abc", "5; rm -rf /"])
def test_logs_rejects_a_bad_line_count(box, lines):
    code, _ = ops(box, "logs", lines)

    assert code != 0
    assert "logs" not in calls(box)


@pytest.mark.parametrize(
    "script,expected_call",
    [
        ("rollback", "pg_restore"),
        ("restore-latest-backup", "dropdb"),
        ("reboot", "shutdown"),
        ("rotate-keys", "pg_dump"),
    ],
)
def test_destructive_scripts_refuse_without_yes(box, backup_set, script, expected_call):
    make_dumps(box, ["postgres-20250101-000000-v1.dump"])

    code, output = ops(box, script, **RESTORE_ENV, BACKUP_SOURCE=str(backup_set))

    assert code != 0
    assert "--yes" in output
    assert expected_call not in calls(box)


def test_reboot_with_yes_schedules_shutdown(box):
    code, output = ops(box, "reboot", "--yes")

    assert code == 0, output
    assert "shutdown -r +1" in calls(box)


def test_disk_cleanup_dry_run_deletes_nothing(box):
    names = [f"postgres-2025010{i}-000000-v1.dump" for i in range(1, 6)]
    make_dumps(box, names)

    code, output = ops(box, "disk-cleanup", "--dry-run", KEEP_BACKUPS="2")

    assert code == 0, output
    assert sorted(p.name for p in (box / "backups").iterdir()) == names
    assert "rm -f -- backups/postgres-20250101-000000-v1.dump" in output
    assert "docker" not in calls(box)


def test_disk_cleanup_prunes_update_and_pre_restore_dumps_separately(box):
    update_dumps = [f"postgres-2025010{i}-000000-v1.dump" for i in range(1, 4)]
    safety_dumps = [f"pre-restore-2025010{i}-000000.dump" for i in range(1, 4)]
    qdrant_tars = [f"pre-restore-qdrant-2025010{i}-000000.tar.gz" for i in range(1, 4)]
    rotate_dumps = [f"pre-rotate-2025010{i}T000000Z.dump" for i in range(1, 4)]
    make_dumps(box, update_dumps + safety_dumps + qdrant_tars + rotate_dumps)

    code, output = ops(box, "disk-cleanup", KEEP_BACKUPS="1")

    assert code == 0, output
    assert sorted(p.name for p in (box / "backups").iterdir()) == sorted(
        [safety_dumps[-1], update_dumps[-1], qdrant_tars[-1], rotate_dumps[-1]]
    )
    assert "docker image prune -af" in calls(box)
    assert "freed_bytes=" in output


@pytest.mark.parametrize(
    "marker, work_kept",
    [("last-backup", False), ("rotate-keys", True)],
    ids=["no-rotation-pending", "rotation-unfinished-or-failed"],
)
def test_disk_cleanup_removes_what_killed_runs_left(box, marker, work_kept):
    """Only day-old partials are from killed runs, and a work dir matters only while a rotation is unsettled."""
    make_dumps(box, ["postgres-20250101-000000-v1.dump.partial", "pre-rotate-1.dump.partial", "keep.dump", "writing.dump.partial"])
    day_old = time.time() - 2 * 86400
    for name in ["postgres-20250101-000000-v1.dump.partial", "pre-rotate-1.dump.partial"]:
        os.utime(box / "backups" / name, (day_old, day_old))
    (box.parent / "state" / "rotate-work.abc").mkdir(parents=True)
    state(box, marker).write_text("failed\n")

    code, output = ops(box, "disk-cleanup")

    assert code == 0, output
    assert sorted(p.name for p in (box / "backups").iterdir()) == ["keep.dump", "writing.dump.partial"]
    assert (box.parent / "state" / "rotate-work.abc").exists() == work_kept


def test_restore_picks_newest_complete_set_and_loads_decrypted_data(box, backup_set):
    add_file_backups(box, backup_set)

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code == 0, output
    log = calls(box)
    assert (box.parent / "calls.log.psql").read_text() == "NEW SQL"
    assert (box.parent / "calls.log.snapshot").read_bytes() == snapshot_tar("new")
    assert "psql -q -v ON_ERROR_STOP=1 --single-transaction" in log
    assert log.index("--storage-snapshot /restore/full.snapshot --force-snapshot") < log.index("dropdb")
    assert "chown -R 1000:1000" in log
    assert (box.parent / "calls.log.fernet").read_text() == "fernet-bytes"
    # .env may not hold the key (on EC2 it lives only in ops.env)
    assert "-e BACKUP_KEYS --entrypoint python worker" in log
    # Never rotated: no release code runs on the restored database.
    assert "memory.common.db.rekey" not in log
    assert (box / "memory_files" / "emails" / "a.eml").read_text() == "mail"
    assert (box / "memory_files" / "ebooks" / "b.epub").read_text() == "book"
    assert not state(box, "restore-failed").exists()


def test_restore_stops_apps_before_taking_the_safety_dump_and_keeps_it(box, backup_set):
    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code == 0, output
    kept = list((box / "backups").glob("pre-restore-*.dump"))
    assert [p.read_text().strip() for p in kept] == ["DUMPDATA"]
    log = calls(box)
    assert log.index("stop api") < log.index("pg_dump") < log.index("dropdb")


def test_restore_reads_s3_with_the_instance_role(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        S3_BACKUP_BUCKET="bucket",
        S3_BACKUP_PREFIX="pfx",
        S3_BACKUP_REGION="eu-west-1",
    )

    assert code == 0, output
    assert "aws s3 ls s3://bucket/pfx/databases/ --region eu-west-1" in calls(box)
    assert (box.parent / "calls.log.psql").read_text() == "NEW SQL"


@pytest.mark.parametrize(
    "missing", ["postgres-20250102.sql.gz.enc", "qdrant-20250102.snapshot.enc"]
)
def test_restore_refuses_an_incomplete_set_before_touching_anything(box, backup_set, missing):
    (backup_set / "databases" / missing).unlink()

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code != 0
    assert "incomplete" in output
    assert "stop" not in calls(box)
    assert "dropdb" not in calls(box)


def test_restore_refuses_a_wrong_key_before_dropping_the_database(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        BACKUP_ENCRYPTION_KEY="wrong",
        BACKUP_SOURCE=str(backup_set),
    )

    assert code != 0
    assert "dropdb" not in calls(box)


@pytest.mark.parametrize("failing", ["psql", "createdb"])
def test_failed_restore_reports_failure_leaves_marker_and_leaks_nothing(
    box, backup_set, failing
):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL=failing,
    )

    assert code != 0
    assert "RESTORE FAILED in step postgres" in output
    assert "pre-restore-2" in output and "pre-restore-qdrant-" in output
    assert "Restored" not in output
    assert "secret-row" not in output
    assert state(box, "restore-failed").exists()
    log = state(box, "restore.log")
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_restore_clears_stale_qdrant_collections_and_restores_files_with_app_ownership(
    box, backup_set
):
    add_file_backups(box, backup_set)

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code == 0, output
    log = calls(box)
    assert "exec -T qdrant bash -c" in log
    assert log.count("chown -R 1000:1000") == 2
    assert oct((box / "memory_files" / "ebooks").stat().st_mode & 0o777) == "0o755"


def test_qdrant_data_is_tarred_before_it_is_replaced_and_kept(box, backup_set):
    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code == 0, output
    log = calls(box)
    assert log.index("--entrypoint tar qdrant -czf") < log.index("--entrypoint find qdrant")
    assert log.index("--entrypoint find qdrant") < log.index("--storage-snapshot")
    assert len(list((box / "backups").glob("pre-restore-qdrant-*.tar.gz"))) == 1


def test_an_empty_qdrant_is_restorable(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_COLLECTIONS="empty",
    )

    assert code == 0, output


@pytest.mark.parametrize("failing", ["qdrant_restore", "qdrant_check"])
def test_failed_qdrant_restore_puts_the_old_data_back_and_leaves_postgres_untouched(
    box, backup_set, failing
):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL=failing,
    )

    assert code != 0
    assert "RESTORE FAILED in step qdrant-restore" in output
    assert "put back from its safety copy" in output
    log = calls(box)
    assert log.index("--storage-snapshot") < log.index("--entrypoint tar qdrant -xzf")
    assert "dropdb" not in log
    assert "psql" not in log
    assert state(box, "restore-failed").exists()


def test_failed_qdrant_safety_copy_changes_nothing(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL="qdrant_tar",
    )

    assert code != 0
    assert "RESTORE FAILED in step qdrant-copy" in output
    assert "Nothing was changed" in output
    assert "--storage-snapshot" not in calls(box)
    assert "dropdb" not in calls(box)


def test_failed_qdrant_stop_changes_nothing(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL="qdrant_stop",
    )

    assert code != 0
    assert "RESTORE FAILED in step qdrant-copy" in output
    assert "Nothing was changed" in output
    assert "--storage-snapshot" not in calls(box)


def test_failed_put_back_says_so_and_names_the_qdrant_copy(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL="qdrant_restore,untar",
    )

    assert code != 0
    assert "RESTORE FAILED in step qdrant-putback-failed" in output
    assert "putting the previous data back failed" in output
    assert "pre-restore-qdrant-" in output
    assert "dropdb" not in calls(box)


def test_failed_file_restore_reports_failure(box, backup_set):
    add_file_backups(box, backup_set)

    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL="fernet",
    )

    assert code != 0
    assert "RESTORE FAILED in step files" in output
    assert "Restored" not in output


def test_corrupt_file_archive_reports_failure_without_listing_members(box, backup_set):
    add_file_backups(box, backup_set)
    (backup_set / "emails.tar.gz.ssl").write_bytes(b"not an archive")

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code != 0
    assert "RESTORE FAILED" in output


def test_restore_refuses_a_snapshot_that_is_not_a_tar_before_stopping_anything(box, backup_set):
    encrypt(b"not a tar", backup_set / "databases/qdrant-20250102.snapshot.enc")

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code != 0
    assert "stop" not in calls(box)


@pytest.mark.parametrize("obj", ["postgres-20250102.sql.gz.enc", "qdrant-20250102.snapshot.enc"])
def test_restore_refuses_a_truncated_object_before_stopping_anything(box, backup_set, obj):
    path = backup_set / "databases" / obj
    path.write_bytes(path.read_bytes()[:-5])

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set)
    )

    assert code != 0
    assert "stop" not in calls(box)
    assert "dropdb" not in calls(box)


def test_restore_waits_for_health_before_reporting_success(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        FAKE_FAIL="health",
        HEALTH_TIMEOUT="1",
    )

    assert code != 0
    assert "RESTORE FAILED in step starting" in output
    assert "started but are not healthy" in output
    assert "are stopped" not in output
    assert "Restored backup set" not in output


@pytest.mark.parametrize(
    "env_line,env",
    [
        (f'BACKUP_ENCRYPTION_KEY="{KEY}"\n', {}),
        ("BACKUP_ENCRYPTION_KEY_FILE={keyfile}\n", {}),
    ],
    ids=["quoted-in-dotenv", "key-file"],
)
def test_restore_finds_the_key_in_dotenv_forms(box, backup_set, env_line, env):
    keyfile = box.parent / "key"
    keyfile.write_text(KEY + "\n")
    (box / ".env").write_text(env_line.format(keyfile=keyfile))

    code, output = ops(box, "restore-latest-backup", "--yes", BACKUP_SOURCE=str(backup_set))

    assert code == 0, output


def test_restore_dry_run_changes_nothing(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--dry-run",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
    )

    assert code == 0, output
    assert "postgres-20250102.sql.gz.enc" in output
    assert "dropdb" not in calls(box)
    assert not (box / "backups").exists()


def release_records(box, record="last-good"):
    """Tag a v2 commit (HEAD) and write the update record `record` for v1 -> v2."""
    git(box, "commit", "--quiet", "--allow-empty", "-m", "v2")
    git(box, "tag", "v2")
    make_dumps(box, ["postgres-20250101-000000-v1.dump"])
    v1, v2 = git(box, "rev-parse", "v1"), git(box, "rev-parse", "v2")
    dump = "backups/postgres-20250101-000000-v1.dump"
    records = {
        "last-good": f"SHA={v2}\nVERSION=v2\nBACKUP={dump}\nPREVIOUS=v1\nPREVIOUS_SHA={v1}\n",
        "update-in-progress": f"PREVIOUS=v1\nPREVIOUS_SHA={v1}\nTARGET_SHA={v2}\nBACKUP={dump}\nMIGRATED=1\n",
    }
    (box / "backups" / record).write_text(records[record])
    return v1, v2


@pytest.mark.parametrize("record", ["last-good", "update-in-progress"])
def test_rollback_restores_dump_checks_out_previous_sha_and_skips_the_bad_tag(
    box, record
):
    v1, _ = release_records(box, record)

    code, output = ops(box, "rollback", "--yes")

    assert code == 0, output
    assert git(box, "rev-parse", "HEAD") == v1
    assert "pg_restore -U kb -d kb --no-owner" in calls(box)
    assert not (box / "backups" / "update-in-progress").exists()
    assert not (box / "backups" / "last-good").exists()
    assert state(box, "skip-tags").read_text().split() == ["v2"]


def test_second_rollback_is_refused(box):
    release_records(box)
    first, _ = ops(box, "rollback", "--yes")
    restores_after_first = calls(box).count("pg_restore -U")

    code, output = ops(box, "rollback", "--yes")

    assert first == 0
    assert code != 0
    assert calls(box).count("pg_restore -U") == restores_after_first


@pytest.mark.parametrize("record", ["last-good", "update-in-progress"])
def test_rollback_refuses_a_record_for_another_checkout(box, record):
    v1, _ = release_records(box, record)
    (box / "backups" / record).write_text(
        f"SHA={v1}\nVERSION=v1\nTARGET_SHA={v1}\nPREVIOUS=v0\nPREVIOUS_SHA={v1}\n"
        "BACKUP=backups/postgres-20250101-000000-v1.dump\nMIGRATED=1\n"
    )

    code, output = ops(box, "rollback", "--yes")

    assert code != 0
    assert "pg_restore" not in calls(box)


def test_rollback_refuses_when_the_last_update_changed_nothing(box):
    v1, v2 = release_records(box)
    (box / "backups" / "last-good").write_text(
        f"SHA={v2}\nVERSION=v2\nBACKUP=backups/postgres-20250101-000000-v1.dump\n"
        f"PREVIOUS=v2\nPREVIOUS_SHA={v2}\n"
    )

    code, output = ops(box, "rollback", "--yes")

    assert code != 0
    assert "pg_restore" not in calls(box)
    assert not state(box, "skip-tags").exists()


@pytest.mark.parametrize("failing", ["pg_restore", "health"])
def test_failed_rollback_reports_failure(box, failing):
    release_records(box)

    code, output = ops(box, "rollback", "--yes", FAKE_FAIL=failing, HEALTH_TIMEOUT="1")

    assert code != 0
    assert "Rolled back" not in output
    assert "secret-row" not in output


def test_update_to_passes_flags_and_prints_only_steps_and_result(box):
    code, output = ops(
        box,
        "update-to",
        "v1.2.3",
        ALLOWED_SIGNERS="/etc/x/signers",
        MIN_AGE_HOURS="72",
    )

    assert code == 0, output
    assert (
        "update.sh --require-signed /etc/x/signers --min-age-hours 72 v1.2.3 lock_held=1"
        in calls(box)
    )
    assert "UPDATE RESULT: ok" in output
    assert "==> Updating" in output
    assert "someone@example.com" not in output


@pytest.mark.parametrize("tag", ["", "master", "v1; rm -rf /", "--dry-run"])
def test_update_to_rejects_anything_but_a_release_tag(box, tag):
    code, _ = ops(box, "update-to", *([tag] if tag else []))

    assert code != 0
    assert "update.sh" not in calls(box)


@pytest.mark.parametrize(
    "script, args",
    [
        ("update-to", ["v1.2.3"]),
        ("rollback", ["--yes"]),
        ("restore-latest-backup", ["--yes"]),
        ("rotate-keys", ["--yes"]),
        ("restart", []),
        ("reboot", ["--yes"]),
        ("disk-cleanup", []),
    ],
)
def test_mutating_scripts_refuse_while_the_nightly_lock_is_held(box, held_lock, script, args):
    code, output = ops(box, script, *args)

    assert code != 0
    assert "busy" in output
    assert calls(box) == ""


def test_a_unit_that_holds_the_lock_can_run_the_scripts(box, held_lock):
    code, output = ops(box, "reboot", "--yes", MEMORY_LOCK_HELD="1")

    assert code == 0, output
    assert "shutdown -r +1" in calls(box)


def test_restart_rejects_unknown_service(box):
    code, output = ops(box, "restart", "nope")

    assert code != 0
    assert "unknown service" in output
    assert "restart" not in calls(box)


def test_scripts_work_embedded_after_lib_without_a_checkout(box, tmp_path):
    ops_dir = box / "tools" / "ops"
    embedded = tmp_path / "embedded" / "status.sh"
    embedded.parent.mkdir()
    embedded.write_text((ops_dir / "lib.sh").read_text() + (ops_dir / "status.sh").read_text())
    shutil.rmtree(ops_dir)

    result = subprocess.run(
        ["bash", str(embedded)],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(tmp_path / "calls.log"),
            "MEMORY_ROOT": str(box),
            "COMPOSE": str(tmp_path / "bin" / "compose"),
            "DOCKER": str(tmp_path / "bin" / "docker"),
            "OPS_ENV": str(tmp_path / "none"),
            "STATE_DIR": str(tmp_path / "state"),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "status=healthy" in result.stdout


def test_embedded_scripts_default_to_opt_memory_and_ignore_a_checkout_less_environment(box, tmp_path):
    ops_dir = box / "tools" / "ops"
    embedded = tmp_path / "embedded" / "status.sh"
    embedded.parent.mkdir()
    embedded.write_text((ops_dir / "lib.sh").read_text() + (ops_dir / "status.sh").read_text())

    result = subprocess.run(
        ["bash", str(embedded)],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "OPS_ENV": str(tmp_path / "none")},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode != 0
    assert "/opt/memory" in result.stderr


def test_a_memory_root_in_the_environment_does_not_redirect_scripts_in_a_checkout(box):
    code, output = ops(box, "status", MEMORY_ROOT="/nonexistent")

    assert code == 0, output


def test_unskip_tag_removes_only_that_tag(box):
    state(box, "skip-tags").parent.mkdir()
    state(box, "skip-tags").write_text("v2\nv3\n")

    code, output = ops(box, "status")  # ensures STATE_DIR exists in env
    result = subprocess.run(
        ["bash", "-c", "source tools/ops/lib.sh; unskip_tag v2"],
        cwd=box,
        env={
            "PATH": f"{box.parent / 'bin'}:/usr/bin:/bin",
            "STATE_DIR": str(box.parent / "state"),
            "OPS_ENV": "/nonexistent",
        },
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert state(box, "skip-tags").read_text().split() == ["v3"]


@pytest.fixture
def local_stack():
    if not shutil.which("docker"):
        pytest.skip("docker is not installed")
    running = subprocess.run(
        ["docker", "compose", "ps", "-q", "api"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if not running.stdout.strip():
        pytest.skip("the local compose stack is not running")


@pytest.mark.local_stack
@pytest.mark.parametrize("script,args", [("status", []), ("disk-cleanup", ["--dry-run"])])
def test_read_only_scripts_work_against_the_local_compose_stack(local_stack, script, args):
    result = subprocess.run(
        ["bash", f"tools/ops/{script}.sh", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "STATE_DIR": "/tmp/memory-ops-test-state"},
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "status=healthy" in result.stdout or script != "status"


@pytest.mark.parametrize("script", sorted((REPO_ROOT / "tools" / "ops").glob("*.sh")), ids=lambda p: p.name)
def test_ops_scripts_have_no_double_braces_ssm_would_read_as_parameters(script):
    assert "{{" not in script.read_text()


def test_status_reports_timers_and_a_failed_update_service(box):
    code, output = ops(
        box,
        "status",
        FAKE_UNITS="memory-update.timer,memory-backup.timer",
        FAKE_FAILED="memory-update.service",
    )

    assert code == 0, output
    lines = output.splitlines()
    assert "memory-update.timer=enabled" in lines
    assert "memory-backup.timer=enabled" in lines
    assert "memory-reboot.timer=missing" in lines
    assert "update_service_failed=yes" in lines


def test_status_reports_the_data_and_root_disks(box):
    code, output = ops(box, "status", DISK_PATH=str(box))

    assert code == 0, output
    assert re.search(r"^disk_used_pct=\d+%$", output, re.M)
    assert re.search(r"^root_disk_used_pct=\d+%$", output, re.M)


def test_restore_reads_a_backup_under_a_superseded_key(box, backup_set):
    """After a rotation the newest backups still use the old keys."""
    add_file_backups(box, backup_set)
    (box / "secrets" / "previous" / "backup_encryption_key").mkdir(parents=True)
    (box / "secrets" / "previous" / "backup_encryption_key" / "20260101T000000Z-1-0").write_text(KEY)
    (box / "secrets" / "previous" / "secrets_encryption_key").mkdir(parents=True)
    (box / "secrets" / "previous" / "secrets_encryption_key" / "20260101T000000Z-1-0").write_text("old-sek")

    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        BACKUP_ENCRYPTION_KEY="the-new-key",
        BACKUP_SOURCE=str(backup_set),
    )

    assert code == 0, output
    assert (box.parent / "calls.log.psql").read_text() == "NEW SQL"
    assert (box / "memory_files" / "emails" / "a.eml").read_text() == "mail"
    log = calls(box)
    assert log.index("psql") < log.index("memory.common.db.rekey") < log.rindex("up -d")
    assert "old-sek" in (box.parent / "calls.log.rekey").read_text()
    assert KEY not in output and "old-sek" not in output


def test_restore_on_ec2_reads_superseded_keys_from_ssm(box, backup_set):
    ssm = box.parent / "ssm" / "memory" / "previous"
    (ssm / "backup_encryption_key").mkdir(parents=True)
    (ssm / "backup_encryption_key" / "20260101T000000Z-1-0").write_text(KEY)
    (ssm / "secrets_encryption_key").mkdir(parents=True)
    (ssm / "secrets_encryption_key" / "20260101T000000Z-1-0").write_text("old-sek")

    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        BACKUP_ENCRYPTION_KEY="the-new-key",
        S3_BACKUP_BUCKET="bucket",
        S3_BACKUP_PREFIX="pfx",
        MEMORY_SSM_PREFIX="/memory",
    )

    assert code == 0, output
    assert (box.parent / "calls.log.psql").read_text() == "NEW SQL"
    assert "old-sek" in (box.parent / "calls.log.rekey").read_text()
    # Downloaded once, though the first key tried is the wrong one.
    assert calls(box).count("databases/postgres-20250102.sql.gz.enc") == 1


def test_restore_refuses_before_stopping_anything_when_ssm_keys_are_unreadable(box, backup_set):
    code, output = ops(
        box,
        "restore-latest-backup",
        "--yes",
        **RESTORE_ENV,
        BACKUP_SOURCE=str(backup_set),
        MEMORY_SSM_PREFIX="/memory",
        FAKE_FAIL="ssm_read",
    )

    assert code != 0
    assert "could not read the kept backup_encryption_key values from SSM" in output
    assert "stop" not in calls(box)


def test_restore_reports_secrets_it_could_not_move(box, backup_set):
    (box / "secrets" / "previous" / "secrets_encryption_key").mkdir(parents=True)
    (box / "secrets" / "previous" / "secrets_encryption_key" / "20260101T000000Z-1-0").write_text("old-sek")

    code, output = ops(
        box, "restore-latest-backup", "--yes", **RESTORE_ENV, BACKUP_SOURCE=str(backup_set), FAKE_FAIL="rekey"
    )

    assert code != 0
    assert "RESTORE FAILED in step secrets" in output
    assert "could not be moved to the current SECRETS_ENCRYPTION_KEY" in output


@pytest.mark.parametrize(
    "previous_files, rekeys_after_build",
    [([], False), (["src/memory/common/db/rekey.py"], True)],
    ids=["previous-predates-rekey", "previous-can-rekey"],
)
def test_rollback_moves_restored_secrets_with_the_release_it_rolls_back_to(
    box, previous_files, rekeys_after_build
):
    """A broken current image can't block the rollback, unless the previous release can't rekey."""
    for name in previous_files:
        (box / name).parent.mkdir(parents=True, exist_ok=True)
        (box / name).write_text("")
    git(box, "add", ".")
    git(box, "commit", "--quiet", "--amend", "-m", "v1")
    git(box, "tag", "-f", "v1")
    release_records(box, "update-in-progress")
    (box / "secrets" / "previous" / "secrets_encryption_key").mkdir(parents=True)
    (box / "secrets" / "previous" / "secrets_encryption_key" / "20260101T000000Z-1-0").write_text("old-sek")

    code, output = ops(box, "rollback", "--yes")

    assert code == 0, output
    log = calls(box)
    assert log.index("pg_restore -U") < log.index("memory.common.db.rekey") < log.rindex("up -d")
    assert (log.index("build") < log.index("memory.common.db.rekey")) == rekeys_after_build


OLD = {
    "SECRETS_ENCRYPTION_KEY": "old-sek-1a",
    "POSTGRES_PASSWORD": "old-pg-3c",
    "REDIS_PASSWORD": "old-redis-4d",
    "BACKUP_ENCRYPTION_KEY": "old-backup-5e",
    "TRANSFER_TOKEN_SECRET": "old-transfer-6f",
}
SECRET_FILES = ["SECRETS_ENCRYPTION_KEY", "POSTGRES_PASSWORD"]
DOTENV_KEYS = ["REDIS_PASSWORD", "BACKUP_ENCRYPTION_KEY", "TRANSFER_TOKEN_SECRET"]
IN_SSM = ["SECRETS_ENCRYPTION_KEY", "POSTGRES_PASSWORD", "REDIS_PASSWORD", "BACKUP_ENCRYPTION_KEY"]


@pytest.fixture
def keyed(box):
    """A box whose secrets/ and .env hold every key rotate-keys generates."""
    (box / "secrets").mkdir(mode=0o700)
    (box.parent / "calls.log.psql").touch()
    for name in SECRET_FILES:
        (box / "secrets" / f"{name.lower()}.txt").write_text(OLD[name])
    (box / ".env").write_text(
        "APP_NAME=memory\n"
        + "".join(f"{name}={OLD[name]}\n" for name in DOTENV_KEYS)
        + "VOYAGE_API_KEY=voyage-secret-0b3e\n"
    )
    return box


def dotenv(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def current_keys(box, env_file=None):
    values = {name: (box / "secrets" / f"{name.lower()}.txt").read_text() for name in SECRET_FILES}
    env = dotenv(env_file or box / ".env")
    return values | {name: env.get(name, "") for name in [*DOTENV_KEYS, "VOYAGE_API_KEY"]}


def keyring(folder, name):
    """Key `name`'s kept values, by entry, in the box's or SSM's previous/ folder."""
    return {p.name: p.read_text() for p in sorted((folder / name.lower()).glob("*"))}


def box_keyring(box, name):
    return keyring(box / "secrets" / "previous", name)


def ssm_values(ssm):
    return {name: (ssm / name.lower()).read_text() for name in IN_SSM}


def test_rotate_keys_replaces_every_generated_key_without_showing_any(keyed):
    code, output = ops(keyed, "rotate-keys", "--yes")

    assert code == 0, output
    new = current_keys(keyed)
    assert all(new[name] != OLD[name] and len(new[name]) >= 32 for name in OLD)
    assert len(set(new[name] for name in OLD)) == len(OLD)
    assert new["VOYAGE_API_KEY"] == "voyage-secret-0b3e"
    log = calls(keyed)
    leaked = [v for v in [*OLD.values(), *(new[n] for n in OLD)] if v in output or v in log]
    assert leaked == []
    assert all(stat.S_IMODE(p.stat().st_mode) == 0o600 for p in [keyed / ".env", *(keyed / "secrets").glob("*.txt")])
    assert f"PASSWORD '{new['POSTGRES_PASSWORD']}'" in (keyed.parent / "calls.log.psql").read_text()
    assert (keyed.parent / "calls.log.rekey").read_text().startswith(
        f"to={new['SECRETS_ENCRYPTION_KEY']} from={OLD['SECRETS_ENCRYPTION_KEY']},"
    )
    assert "secrets_reencrypted=3" in output
    assert log.index("pg_dump") < log.index("stop api") < log.index("psql -q") < log.index("up -d --force-recreate")
    assert [p.read_text() for p in (keyed / "backups").glob("pre-rotate-*.dump")] == ["DUMPDATA\n"]
    assert not list((keyed / "backups").glob("*.partial"))
    assert not state(keyed, "rotate-keys").exists()
    assert "status=healthy" in output


def test_rotate_keys_keeps_the_old_and_new_keys_backups_need(keyed):
    code, output = ops(keyed, "rotate-keys", "--yes")

    assert code == 0, output
    new = current_keys(keyed)
    assert list(box_keyring(keyed, "BACKUP_ENCRYPTION_KEY").values()) == [OLD["BACKUP_ENCRYPTION_KEY"], new["BACKUP_ENCRYPTION_KEY"]]
    assert list(box_keyring(keyed, "SECRETS_ENCRYPTION_KEY").values()) == [OLD["SECRETS_ENCRYPTION_KEY"], new["SECRETS_ENCRYPTION_KEY"]]
    assert box_keyring(keyed, "POSTGRES_PASSWORD") == {}
    assert stat.S_IMODE((keyed / "secrets" / "previous").stat().st_mode) == 0o700


def test_a_second_rotation_keeps_every_key_used(keyed):
    ops(keyed, "rotate-keys", "--yes", "--only=BACKUP_ENCRYPTION_KEY")
    first = current_keys(keyed)["BACKUP_ENCRYPTION_KEY"]

    code, output = ops(keyed, "rotate-keys", "--yes", "--only=BACKUP_ENCRYPTION_KEY")

    assert code == 0, output
    second = current_keys(keyed)["BACKUP_ENCRYPTION_KEY"]
    assert list(box_keyring(keyed, "BACKUP_ENCRYPTION_KEY").values()) == [OLD["BACKUP_ENCRYPTION_KEY"], first, first, second]


@pytest.mark.parametrize(
    "only, rotated",
    [
        (["--only", "REDIS_PASSWORD"], {"REDIS_PASSWORD"}),
        (["--only=POSTGRES_PASSWORD,REDIS_PASSWORD"], {"POSTGRES_PASSWORD", "REDIS_PASSWORD"}),
        (["--only="], set(OLD)),
    ],
)
def test_rotate_keys_only_rotates_the_named_keys(keyed, only, rotated):
    code, output = ops(keyed, "rotate-keys", "--yes", *only)

    assert code == 0, output
    new = current_keys(keyed)
    assert {name for name in OLD if new[name] != OLD[name]} == rotated
    assert ("memory.common.db.rekey" in calls(keyed)) == ("SECRETS_ENCRYPTION_KEY" in rotated)
    assert ("psql -q" in calls(keyed)) == ("POSTGRES_PASSWORD" in rotated)


@pytest.mark.parametrize("name", ["VOYAGE_API_KEY", "ADMIN_PASSWORD", "JWT_SECRET", "redis_password", "X;rm"])
def test_rotate_keys_refuses_keys_it_does_not_generate(keyed, name):
    code, output = ops(keyed, "rotate-keys", "--yes", f"--only={name}")

    assert code == 2
    assert "unknown key" in output
    assert calls(keyed) == ""


def test_rotate_keys_says_a_derived_transfer_secret_rotates_with_the_secrets_key(keyed):
    (keyed / ".env").write_text("REDIS_PASSWORD=old-redis-4d\n")

    code, output = ops(keyed, "rotate-keys", "--yes")

    assert code == 0, output
    assert "TRANSFER_TOKEN_SECRET: derived from SECRETS_ENCRYPTION_KEY" in output
    assert "BACKUP_ENCRYPTION_KEY: not configured" in output
    assert "TRANSFER_TOKEN_SECRET" not in (keyed / ".env").read_text()


def test_rotate_keys_dry_run_changes_nothing(keyed):
    code, output = ops(keyed, "rotate-keys", "--dry-run")

    assert code == 0, output
    assert {name: current_keys(keyed)[name] for name in OLD} == OLD
    assert not (keyed / "backups").exists()
    assert "stop" not in calls(keyed)


def test_rotate_keys_refuses_without_room_for_the_dump(keyed):
    code, output = ops(keyed, "rotate-keys", "--yes", FAKE_DB_BYTES=str(2**60))

    assert code == 1
    assert "Not enough disk" in output
    assert "pg_dump" not in calls(keyed)
    assert {name: current_keys(keyed)[name] for name in OLD} == OLD


def test_rotate_keys_keeps_only_the_newest_pre_rotate_dumps(keyed):
    make_dumps(keyed, [f"pre-rotate-2025010{i}T000000Z-1.dump" for i in range(1, 4)])

    code, output = ops(keyed, "rotate-keys", "--yes", KEEP_BACKUPS="2")

    assert code == 0, output
    names = sorted(p.name for p in (keyed / "backups").glob("pre-rotate-*.dump"))
    assert names[0] == "pre-rotate-20250103T000000Z-1.dump"
    assert len(names) == 2


def ec2(box):
    """The EC2 layout: the backup key only in ops.env, every generated key in SSM."""
    ops_env = box.parent / "ops.env"
    ops_env.write_text(f"BACKUP_ENCRYPTION_KEY={OLD['BACKUP_ENCRYPTION_KEY']}\nS3_BACKUP_BUCKET=bucket\n")
    env = dotenv(box / ".env")
    del env["BACKUP_ENCRYPTION_KEY"]
    (box / ".env").write_text("".join(f"{k}={v}\n" for k, v in env.items()))
    ssm = box.parent / "ssm" / "memory"
    ssm.mkdir(parents=True)
    for name in IN_SSM:
        (ssm / name.lower()).write_text(OLD[name])
    return ops_env, ssm


def ec2_keys(box, ops_env):
    return current_keys(box) | {"BACKUP_ENCRYPTION_KEY": dotenv(ops_env)["BACKUP_ENCRYPTION_KEY"]}


def rotate_on_ec2(box, ops_env, **env):
    return ops(box, "rotate-keys", "--yes", OPS_ENV=str(ops_env), MEMORY_SSM_PREFIX="/memory", **env)


def test_rotate_keys_on_ec2_updates_ssm_and_ops_env(keyed):
    ops_env, ssm = ec2(keyed)

    code, output = rotate_on_ec2(keyed, ops_env)

    assert code == 0, output
    new = ec2_keys(keyed, ops_env)
    assert dotenv(ops_env)["S3_BACKUP_BUCKET"] == "bucket"
    assert ssm_values(ssm) == {name: new[name] for name in IN_SSM}
    assert not set(ssm_values(ssm).values()) & set(OLD.values())
    assert {(keyed.parent / "ssm.kms" / "memory" / name.lower()).read_text() for name in IN_SSM} == {"alias/memory"}
    assert {name: list(keyring(ssm / "previous", name).values()) for name in ["SECRETS_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY"]} == {
        name: [OLD[name], new[name]] for name in ["SECRETS_ENCRYPTION_KEY", "BACKUP_ENCRYPTION_KEY"]
    }
    assert not (ssm / "previous" / "postgres_password").exists()
    leaked = [v for v in [*OLD.values(), *(new[n] for n in OLD)] if v in output or v in calls(keyed)]
    assert leaked == []
    assert not list(keyed.parent.glob("state/rotate-work.*"))


@pytest.mark.parametrize("failing, postgres_changed", [("rekey", True), ("ssm_put", False)])
def test_failed_rotation_puts_every_old_key_back(keyed, failing, postgres_changed):
    ops_env, ssm = ec2(keyed)
    before = current_keys(keyed)

    code, output = rotate_on_ec2(keyed, ops_env, FAKE_FAIL=failing)

    assert code != 0
    assert "ROTATION FAILED" in output
    assert "previous keys are back" in output
    assert current_keys(keyed) == before
    assert dotenv(ops_env)["BACKUP_ENCRYPTION_KEY"] == OLD["BACKUP_ENCRYPTION_KEY"]
    assert ssm_values(ssm) == {name: OLD[name] for name in IN_SSM}
    psql = (keyed.parent / "calls.log.psql").read_text()
    assert psql.endswith(f"PASSWORD '{OLD['POSTGRES_PASSWORD']}';\n") == postgres_changed
    assert calls(keyed).count("up -d --force-recreate") == 1
    assert state(keyed, "rotate-keys").read_text() == "failed kept_new= unsettled=\n"
    assert all(v not in output for v in OLD.values())


def test_rotation_that_leaves_the_stack_unhealthy_puts_the_old_keys_back(keyed):
    code, output = ops(keyed, "rotate-keys", "--yes", FAKE_FAIL="health", HEALTH_TIMEOUT="1")

    assert code != 0
    assert "ROTATION FAILED in step starting" in output
    assert "not healthy" in output
    assert {name: current_keys(keyed)[name] for name in OLD} == OLD
    rekeys = (keyed.parent / "calls.log.rekey").read_text().splitlines()
    assert rekeys[-1].startswith(f"to={OLD['SECRETS_ENCRYPTION_KEY']} from=")


def test_a_failed_undo_keeps_the_secrets_key_the_database_is_on(keyed):
    ops_env, ssm = ec2(keyed)

    code, output = rotate_on_ec2(keyed, ops_env, FAKE_FAIL="health,rekey_back", HEALTH_TIMEOUT="1")

    assert code != 0
    now = ec2_keys(keyed, ops_env)
    secrets_key = now["SECRETS_ENCRYPTION_KEY"]
    others = ["POSTGRES_PASSWORD", "REDIS_PASSWORD", "BACKUP_ENCRYPTION_KEY", "TRANSFER_TOKEN_SECRET"]
    assert (keyed.parent / "calls.log.rekey").read_text().startswith(f"to={secrets_key} ")
    assert ssm_values(ssm)["SECRETS_ENCRYPTION_KEY"] == secrets_key != OLD["SECRETS_ENCRYPTION_KEY"]
    assert list(box_keyring(keyed, "SECRETS_ENCRYPTION_KEY").values()) == [OLD["SECRETS_ENCRYPTION_KEY"], secrets_key]
    assert {name: now[name] for name in others} == {name: OLD[name] for name in others}
    assert "The database is on the new SECRETS_ENCRYPTION_KEY" in output
    assert "--only=SECRETS_ENCRYPTION_KEY" in output
    assert state(keyed, "rotate-keys").read_text() == "failed kept_new=SECRETS_ENCRYPTION_KEY unsettled=\n"
    assert secrets_key not in output


def test_a_rotation_that_cannot_be_undone_says_so_and_keeps_the_old_files(keyed):
    code, output = ops(keyed, "rotate-keys", "--yes", FAKE_FAIL="psql")

    assert code != 0
    assert "ROTATION FAILED in step postgres" in output
    assert "failed too" in output
    assert "previous keys are back" not in output
    assert "--only=POSTGRES_PASSWORD again" in output
    assert state(keyed, "rotate-keys").read_text() == "failed kept_new= unsettled=POSTGRES_PASSWORD\n"
    (work,) = keyed.parent.glob("state/rotate-work.*")
    assert stat.S_IMODE(work.stat().st_mode) == 0o700
    assert {name: current_keys(keyed)[name] for name in OLD} == OLD
    assert all(v not in output for v in OLD.values())


@pytest.mark.parametrize(
    "marker, line",
    [
        ("started 20260101T000000Z-1\n", "rotate_keys=unfinished since="),
        (
            "failed kept_new=SECRETS_ENCRYPTION_KEY,POSTGRES_PASSWORD unsettled=REDIS_PASSWORD\n",
            "kept_new=SECRETS_ENCRYPTION_KEY,POSTGRES_PASSWORD unsettled=REDIS_PASSWORD",
        ),
    ],
)
def test_status_reports_an_unfinished_or_failed_rotation(box, marker, line):
    state(box, "rotate-keys").parent.mkdir(parents=True, exist_ok=True)
    state(box, "rotate-keys").write_text(marker)

    code, output = ops(box, "status")

    assert code == 0, output
    assert line in output
