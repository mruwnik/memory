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
