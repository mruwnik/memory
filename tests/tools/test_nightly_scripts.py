import datetime
import os
import pathlib
import re
import shutil
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).parents[2]
SIGNERS = '* namespaces="git" ssh-ed25519 AAAAnew signer@example.com'
CREDS = {
    "AWS_ACCESS_KEY_ID": "ASIAFAKEKEY",
    "AWS_SECRET_ACCESS_KEY": "fake-secret-9d2",
    "AWS_SESSION_TOKEN": "fake-token-41c",
}

# Parameters live as files under $FAKE_SSM; credentials come from the CLI's own
# chain, as on the instance. FAKE_FAIL is a comma list: ssm, upload.
FAKE_AWS = """#!/usr/bin/env bash
echo "aws $* | key=${AWS_ACCESS_KEY_ID:-}" >> "$FAKE_LOG"
fails() { [[ ,${FAKE_FAIL:-}, == *,$1,* ]]; }
case "$1 $2" in
    "ssm get-parameter")
        fails ssm && exit 1
        name=$(sed -n 's/.*--name \\([^ ]*\\).*/\\1/p' <<<"$*")
        cat "$FAKE_SSM/${name##*/}" || exit 1 ;;
    "configure export-credentials")
        # FAKE_CRED_EXPIRIES: one expiry per call, the last one repeating.
        n=$(cat "$FAKE_LOG.creds" 2>/dev/null || echo 0)
        echo $((n + 1)) > "$FAKE_LOG.creds"
        IFS=, read -ra expiries <<<"${FAKE_CRED_EXPIRIES:-2099-01-01T00:00:00Z}"
        ((n < ${#expiries[@]})) || n=$((${#expiries[@]} - 1))
        printf 'AWS_ACCESS_KEY_ID=ASIAFAKEKEY\\nAWS_SECRET_ACCESS_KEY=fake-secret-9d2\\n'
        printf 'AWS_SESSION_TOKEN=fake-token-41c\\nAWS_CREDENTIAL_EXPIRATION=%s\\n' "${expiries[n]}" ;;
    "s3 cp"|"s3 sync") fails upload && exit 1 ;;
esac
exit 0
"""
# FAKE_FAIL steps: databases (the backup service run), files (the worker run).
FAKE_COMPOSE = """#!/usr/bin/env bash
echo "compose ${*//$'\\n'/ } | key=${AWS_ACCESS_KEY_ID:-} max=${MAX_BACKUPS:-}" >> "$FAKE_LOG"
fails() { [[ ,${FAKE_FAIL:-}, == *,$1,* ]]; }
case "$*" in
    *" backup") fails databases && exit 1 ;;
    *"--entrypoint python worker"*)
        fails files && exit 1
        for a in "$@"; do [[ $a == *:/out ]] && out=${a%:/out}; done
        echo encrypted > "$out/notes.tar.gz.enc"
        echo "sync ebooks" ;;
esac
exit 0
"""
FAKE_SELECT = """#!/usr/bin/env bash
echo "select $*" >> "$FAKE_LOG"
[[ ${FAKE_SELECT_RC:-0} == 0 ]] && echo v2
exit "${FAKE_SELECT_RC:-0}"
"""
FAKE_UPDATE = """#!/usr/bin/env bash
echo "update.sh $*" >> "$FAKE_LOG"
exit "${FAKE_UPDATE_RC:-0}"
"""
FAKE_SYSTEMCTL = """#!/usr/bin/env bash
echo "systemctl $*" >> "$FAKE_LOG"
"""
FAKE_CHOWN = """#!/usr/bin/env bash
echo "chown $*" >> "$FAKE_LOG"
"""


def git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def write_exe(path: pathlib.Path, body: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def host(tmp_path):
    """A checkout with the real ops and host scripts, fakes for everything that
    touches AWS, compose or the release tools, and an ops.env like bootstrap's."""
    repo = tmp_path / "repo"
    shutil.copytree(REPO_ROOT / "tools" / "ops", repo / "tools" / "ops")
    shutil.copytree(REPO_ROOT / "deploy" / "aws" / "host", repo / "deploy" / "aws" / "host")
    write_exe(repo / "tools" / "select_release.sh", FAKE_SELECT)
    write_exe(repo / "tools" / "update.sh", FAKE_UPDATE)
    (repo / "memory_files" / "ebooks").mkdir(parents=True)
    git(repo, "init", "--quiet")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "v1")
    git(repo, "remote", "add", "origin", str(repo))
    for name, body in {
        "aws": FAKE_AWS,
        "compose": FAKE_COMPOSE,
        "chown": FAKE_CHOWN,
        "systemctl": FAKE_SYSTEMCTL,
    }.items():
        write_exe(tmp_path / "bin" / name, body)
    ssm = tmp_path / "ssm"
    ssm.mkdir()
    for name, value in {
        "release_signers": SIGNERS,
        "auto_update": "true",
        "update_delay_hours": "72",
        "domain": "memory.example.com",
        "backup_encryption_key": "backup-key-5e1",
        "s3_backup_bucket": "bucket",
        "s3_backup_prefix": "pfx",
        "s3_backup_region": "eu-west-1",
    }.items():
        (ssm / name).write_text(value + "\n")
    (tmp_path / "allowed_signers").write_text("* old-signer\n")
    (tmp_path / "units").mkdir()
    (tmp_path / "ops.env").write_text(
        "BACKUP_ENCRYPTION_KEY=backup-key-5e1\n"
        "S3_BACKUP_BUCKET=bucket\nS3_BACKUP_PREFIX=pfx\nS3_BACKUP_REGION=eu-west-1\n"
        "MIN_AGE_HOURS=48\nMEMORY_SSM_PREFIX=/memory\nAWS_DEFAULT_REGION=eu-west-1\n"
    )
    return repo


def nightly(host, name, **env):
    tmp = host.parent
    result = subprocess.run(
        ["bash", f"deploy/aws/host/{name}.sh"],
        cwd=host,
        stdin=subprocess.DEVNULL,
        env={
            "PATH": f"{tmp / 'bin'}:/usr/bin:/bin",
            "FAKE_LOG": str(tmp / "calls.log"),
            "FAKE_SSM": str(tmp / "ssm"),
            "COMPOSE": str(tmp / "bin" / "compose"),
            "OPS_ENV": str(tmp / "ops.env"),
            "ALLOWED_SIGNERS": str(tmp / "allowed_signers"),
            "CADDYFILE": str(tmp / "Caddyfile"),
            "UNIT_DIR": str(tmp / "units"),
            "MEMORY_SSM_PREFIX": "/memory",
            "AWS_DEFAULT_REGION": "eu-west-1",
            "STATE_DIR": str(tmp / "state"),
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr


def calls(host):
    log = host.parent / "calls.log"
    return log.read_text() if log.exists() else ""


def call_lines(host, word):
    return [line for line in calls(host).splitlines() if line.startswith(word)]


def test_update_rereads_ssm_and_installs_the_selected_release(host):
    code, output = nightly(host, "nightly-update")

    assert code == 0, output
    assert (host.parent / "allowed_signers").read_text() == SIGNERS + "\n"
    ops_env = (host.parent / "ops.env").read_text().splitlines()
    assert "MIN_AGE_HOURS=72" in ops_env
    assert "MIN_AGE_HOURS=48" not in ops_env
    assert "S3_BACKUP_BUCKET=bucket" in ops_env
    signers = str(host.parent / "allowed_signers")
    assert call_lines(host, "select") == [f"select --require-signed {signers} --min-age-hours 72"]
    assert call_lines(host, "update.sh") == [
        f"update.sh --require-signed {signers} --min-age-hours 72 v2"
    ]


# The units of an updated box come from the release it now runs. A failed
# tag is skipped from then on, so a fixed, newer release still gets in.
@pytest.mark.parametrize(
    "update_rc, reconfigured, skipped, marker",
    [("0", True, [], "ok"), ("1", False, ["v2"], "failed")],
)
def test_update_after_update_sh(host, update_rc, reconfigured, skipped, marker):
    skip_tags = host.parent / "state" / "skip-tags"
    skip_tags.parent.mkdir()
    skip_tags.touch()

    code, output = nightly(host, "nightly-update", FAKE_UPDATE_RC=update_rc)

    assert (code == 0) == reconfigured, output
    after_update = calls(host).split("update.sh ", 1)[1]
    assert ("systemctl daemon-reload" in after_update) == reconfigured
    assert skip_tags.read_text().split() == skipped
    assert (host.parent / "state" / "last-update-check").read_text().split()[0] == marker


def test_update_runs_after_an_earlier_failed_update(host):
    (host.parent / "state").mkdir()
    (host.parent / "state" / "update-failed").write_text("2026-01-01T00:00:00Z")

    code, output = nightly(host, "nightly-update")

    assert code == 0, output
    assert len(call_lines(host, "update.sh")) == 1


def test_configure_renders_ssm_config_units_and_caddy(host):
    code, output = nightly(host, "configure")

    assert code == 0, output
    tmp = host.parent
    assert (tmp / "allowed_signers").read_text() == SIGNERS + "\n"
    assert (tmp / "ops.env").read_text().splitlines() == [
        "BACKUP_ENCRYPTION_KEY=backup-key-5e1",
        "S3_BACKUP_BUCKET=bucket",
        "S3_BACKUP_PREFIX=pfx",
        "S3_BACKUP_REGION=eu-west-1",
        "MIN_AGE_HOURS=72",
        "MEMORY_SSM_PREFIX=/memory",
        "AWS_DEFAULT_REGION=eu-west-1",
        "DISK_PATH=/srv/memory",
    ]
    assert (tmp / "Caddyfile").read_text() == "memory.example.com {\n\treverse_proxy 127.0.0.1:8000\n}\n"
    units = sorted(p.name for p in (REPO_ROOT / "deploy" / "aws" / "host").glob("memory-*"))
    assert sorted(p.name for p in (tmp / "units").iterdir()) == sorted(
        [*units, "apt-daily-upgrade.service.d"]
    )
    assert "flock /run/lock/memory-nightly.lock" in (
        tmp / "units" / "apt-daily-upgrade.service.d" / "memory.conf"
    ).read_text()
    # update.sh would otherwise refuse the lock its unit holds.
    assert "Environment=MEMORY_LOCK_HELD=1" in (tmp / "units" / "memory-update.service").read_text()
    timers = " ".join(u for u in units if u.endswith(".timer"))
    assert call_lines(host, "systemctl") == [
        "systemctl reload-or-restart caddy",
        "systemctl daemon-reload",
        f"systemctl enable --now {timers}",
    ]


def test_configure_removes_units_the_release_no_longer_ships(host):
    for name in ["memory-old.service", "memory-old.timer"]:
        (host.parent / "units" / name).write_text("[Unit]\n")

    code, output = nightly(host, "configure")

    assert code == 0, output
    assert not list((host.parent / "units").glob("memory-old.*"))
    systemctl = call_lines(host, "systemctl")
    assert systemctl.index("systemctl disable --now memory-old.timer") < systemctl.index(
        "systemctl daemon-reload"
    )


def test_configure_reloads_caddy_only_when_the_domain_changes(host):
    nightly(host, "configure")
    unchanged = call_lines(host, "systemctl")
    (host.parent / "ssm" / "domain").write_text("other.example.com\n")
    nightly(host, "configure")

    assert unchanged.count("systemctl reload-or-restart caddy") == 1
    assert call_lines(host, "systemctl").count("systemctl reload-or-restart caddy") == 2


@pytest.mark.parametrize(
    "auto_update, select_rc, selects, code, marker",
    [
        ("false", 0, False, 0, "ok"),  # off: releases arrive only by update-to
        ("true", 3, True, 0, "ok"),  # up to date
        ("true", 1, True, 1, "failed"),  # nothing qualifies
    ],
)
def test_update_skips_update_sh(host, auto_update, select_rc, selects, code, marker):
    (host.parent / "ssm" / "auto_update").write_text(auto_update + "\n")

    got, output = nightly(host, "nightly-update", FAKE_SELECT_RC=str(select_rc))

    assert got == code, output
    assert bool(call_lines(host, "select")) == selects
    assert call_lines(host, "update.sh") == []
    assert (host.parent / "state" / "last-update-check").read_text().split()[0] == marker
    # Signers are refreshed even when nothing installs, so update-to sees a revocation.
    assert (host.parent / "allowed_signers").read_text() == SIGNERS + "\n"


@pytest.mark.parametrize(
    "param, value",
    [
        ("release_signers", ""),
        ("update_delay_hours", "soon"),
        ("auto_update", "maybe"),
        ("domain", "a b;reboot"),
    ],
)
def test_update_keeps_the_old_config_when_ssm_is_unusable(host, param, value):
    (host.parent / "ssm" / param).write_text(value + "\n")

    code, output = nightly(host, "nightly-update")

    assert code != 0, output
    assert (host.parent / "allowed_signers").read_text() == "* old-signer\n"
    assert "MIN_AGE_HOURS=48" in (host.parent / "ops.env").read_text()
    assert call_lines(host, "update.sh") == []


def test_a_reset_backup_key_keeps_the_box_key_but_still_renders_the_rest(host):
    """A signer the owner revoked must reach the box even while the update is held back."""
    (host.parent / "ssm" / "backup_encryption_key").write_text("reset-key\n")

    code, output = nightly(host, "nightly-update")

    assert code != 0, output
    assert "backup_encryption_key differs" in output
    assert (host.parent / "allowed_signers").read_text() == SIGNERS + "\n"
    ops_env = (host.parent / "ops.env").read_text().splitlines()
    assert ["BACKUP_ENCRYPTION_KEY=backup-key-5e1", "MIN_AGE_HOURS=72"] == [ops_env[0], ops_env[4]]
    assert "reset-key" not in output
    assert call_lines(host, "update.sh") == []


def test_update_fails_without_touching_config_when_ssm_is_unreachable(host):
    code, output = nightly(host, "nightly-update", FAKE_FAIL="ssm")

    assert code != 0, output
    assert (host.parent / "allowed_signers").read_text() == "* old-signer\n"
    assert call_lines(host, "update.sh") == []


def test_backup_passes_role_credentials_only_to_the_database_backup(host):
    code, output = nightly(host, "nightly-backup")

    assert code == 0, output
    compose = call_lines(host, "compose")
    (db_run,) = [c for c in compose if c.split(" | ")[0].endswith(" backup")]
    (worker_run,) = [c for c in compose if "worker" in c]
    for name in CREDS:
        assert f"-e {name}" in db_run
        assert f"-e {name}" not in worker_run
    assert "key=ASIAFAKEKEY" in db_run
    assert "key= " in worker_run
    for value in CREDS.values():
        assert value not in output
    # The role can't delete; the bucket lifecycle expires old backups.
    assert "max=0" in db_run
    assert all(c.endswith("| key=") for c in call_lines(host, "aws s3"))


def test_backup_uploads_files_under_the_workers_keys(host):
    code, output = nightly(host, "nightly-backup")

    assert code == 0, output
    s3 = call_lines(host, "aws s3")
    assert re.search(r"^aws s3 cp \S+/notes\.tar\.gz\.enc s3://bucket/pfx/notes\.tar\.gz\.enc ", calls(host), re.M)
    (sync,) = [c for c in s3 if "s3 sync" in c]
    assert f"{host}/memory_files/ebooks s3://bucket/pfx/ebooks" in sync
    assert "--delete" not in sync
    assert not list((host.parent / "state").glob("backup-stage.*"))


def test_backup_writes_ok_marker(host):
    code, output = nightly(host, "nightly-backup")

    assert code == 0, output
    assert (host.parent / "state" / "last-backup").read_text().split()[0] == "ok"


@pytest.mark.parametrize("step", ["databases", "files", "upload"])
def test_backup_writes_failed_marker(host, step):
    code, output = nightly(host, "nightly-backup", FAKE_FAIL=step)

    assert code != 0, output
    assert (host.parent / "state" / "last-backup").read_text().split()[0] == "failed"
    # One half failing doesn't stop the other.
    assert call_lines(host, "compose") and any("worker" in c for c in call_lines(host, "compose"))


def iso_in(seconds):
    t = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# A backup can outlast credentials that are close to expiring.
@pytest.mark.parametrize(
    "expiries, wait, ok, fetches",
    [
        ([iso_in(600), iso_in(6 * 3600)], "60", True, 2),  # refreshed while waiting
        ([iso_in(600)], "0", False, 1),  # never refreshed
    ],
)
def test_backup_waits_for_fresh_credentials(host, expiries, wait, ok, fetches):
    code, output = nightly(
        host,
        "nightly-backup",
        FAKE_CRED_EXPIRIES=",".join(expiries),
        CRED_WAIT_SECONDS=wait,
        CRED_POLL_SECONDS="0",
    )

    assert (code == 0) == ok, output
    assert (host.parent / "calls.log.creds").read_text().strip() == str(fetches)
    db_runs = [c for c in call_lines(host, "compose") if c.split(" | ")[0].endswith(" backup")]
    assert len(db_runs) == int(ok)
    assert ("did not refresh" in output) == (not ok)


def test_encrypt_snippet_produces_what_restore_decrypts(tmp_path):
    """Pins the contract with restore-latest-backup: Fernet archives under the
    worker's names, and plain directories listed for syncing."""
    body = (REPO_ROOT / "deploy" / "aws" / "host" / "nightly-backup.sh").read_text()
    snippet = re.findall(r"^ENCRYPT='(.*?)'$", body, re.S | re.M)[0]
    files, out = tmp_path / "files", tmp_path / "out"
    (files / "notes").mkdir(parents=True)
    (files / "notes" / "a.md").write_text("note")
    out.mkdir()
    env = {
        **os.environ,
        "FILE_STORAGE_DIR": str(files),
        "BACKUP_ENCRYPTION_KEY": "k-test",
        "PYTHONPATH": str(REPO_ROOT / "src"),
    }

    manifest = subprocess.run(
        [sys.executable, "-c", snippet.replace('Path("/out")', f"Path({str(out)!r})")],
        env=env, capture_output=True, text=True, check=True, timeout=120,
    ).stdout  # fmt: skip

    assert "sync ebooks" in manifest.splitlines()
    assert "notes.tar.gz.enc" in {p.name for p in out.iterdir()}
    restore = (REPO_ROOT / "tools" / "ops" / "restore-latest-backup.sh").read_text()
    fernet = re.findall(r"^FERNET='(.*?)'$", restore, re.S | re.M)[0]
    restored = tmp_path / "restored"
    # The archive's key is a superseded one: restore tries every key it knows.
    subprocess.run(
        [sys.executable, "-c", fernet.replace('"/app/memory_files"', repr(str(restored)))],
        input=(out / "notes.tar.gz.enc").read_bytes(),
        env={**env, "BACKUP_ENCRYPTION_KEY": "k-current", "BACKUP_KEYS": "k-current\nk-test"},
        capture_output=True, check=True, timeout=120,
    )  # fmt: skip
    assert (restored / "notes" / "a.md").read_text() == "note"
