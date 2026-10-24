import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).parents[2] / "tools" / "backup_databases.sh"


@pytest.mark.parametrize(
    "env, missing",
    [
        ({}, "S3_BACKUP_BUCKET"),
        ({"BACKUP_ENCRYPTION_KEY": "k"}, "S3_BACKUP_BUCKET"),
        ({"S3_BACKUP_BUCKET": "b"}, "BACKUP_ENCRYPTION_KEY"),
    ],
)
def test_refuses_without_bucket_and_key(env, missing):
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", **env},
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode != 0
    assert missing in result.stderr


FAKE_AWS = """#!/usr/bin/env bash
echo "aws $*" >> "$FAKE_LOG"
case "$2" in
    ls) printf '2025-01-01 00:00:00 1 postgres-20250101.sql.gz.enc\\n2025-01-02 00:00:00 1 postgres-20250102.sql.gz.enc\\n'
        printf '2025-01-01 00:00:00 1 qdrant-20250101.snapshot.enc\\n2025-01-02 00:00:00 1 qdrant-20250102.snapshot.enc\\n' ;;
    cp) cat > /dev/null ;;
esac
"""
FAKE_WGET = """#!/usr/bin/env bash
[[ $* == *--post-data* ]] && echo '{"result":{"name":"snap1"}}' || echo snapshot-bytes
"""


@pytest.mark.parametrize("max_backups, deleted", [("1", True), ("0", False)])
def test_max_backups_zero_never_prunes(tmp_path, max_backups, deleted):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in {
        "aws": FAKE_AWS,
        "wget": FAKE_WGET,
        "pg_dump": "#!/bin/sh\necho SQL\n",
    }.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    (tmp_path / "pg").write_text("pw")
    log = tmp_path / "calls.log"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "FAKE_LOG": str(log),
            "S3_BACKUP_BUCKET": "b",
            "BACKUP_ENCRYPTION_KEY": "k",
            "POSTGRES_PASSWORD_FILE": str(tmp_path / "pg"),
            "MAX_BACKUPS": max_backups,
        },
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert ("aws s3 rm" in log.read_text()) == deleted
